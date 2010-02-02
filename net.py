#!/usr/bin/python

import sys,thread,os
import socket,select,errno,code,re,fcntl,struct

from misc import proputil,flag_str, DynInit
from statemachine import FuncSM, StreamReader

version=(0,2,20090924)

debug=False

def nice_shutdown(sock):
	try: sock.shutdown(2)
	except socket.error,e:
		if e[0] in (errno.ENOTCONN,errno.EBADF): pass
		else: raise
	sock.close()

ETH_P_ALL=3

class EndOfDataException(Exception): pass

class Interface(object):
	SIOCGIFADDR=0x8915
	SIOCGIFNETMASK=0x891b
	SIOCGIFHWADDR=0x8927
	def __getattr__(self,key):
		if not key.startswith("get_"): return getattr(self,"get_%s"%key,lambda: object.__getattribute__(self,key))()
		return object.__getattribute__(self,key)
	def __init__(self,name):
		self.name=name
	def get_sock(self):
		return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	def get_ip(self):
		return socket.inet_ntoa(fcntl.ioctl(self.sock.fileno(),self.SIOCGIFADDR,struct.pack('256s',self.name))[20:24])
	def get_netmask(self):
		return socket.inet_ntoa(fcntl.ioctl(self.sock.fileno(),self.SIOCGIFNETMASK,struct.pack('256s',self.name))[20:24])
	def get_mac(self):
		return ':'.join('%02x'%(ord(x)) for x in fcntl.ioctl(self.sock.fileno(),self.SIOCGIFHWADDR,struct.pack('256s',self.name))[18:24])
	def send(self,data):
		return self.rawsock.send(data)
	def recv(self,size=None):
		if size is None: size=self.sock.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF)
		return self.rawsock.recv(size)
	def get_rawsock(self):
		rawsock=socket.socket(socket.PF_PACKET,socket.SOCK_RAW,socket.htons(ETH_P_ALL))
		rawsock.bind((self.name,0))
		return rawsock
	def __del__(self):
		if 'rawsock' in self.__dict__: self.rawsock.close()
		if 'sock' in self.__dict__: self.sock.close()

class NetSock(object):
	recv_size=1500
	verbose=True
	def __getattr__(self,key):
		return getattr(self.sock,key)
	def interact(self,input=sys.stdin,output=sys.stdout):
		print "Type now!"
		while 1:
			try: in_set,out_set,err_set=select.select([self.sock,input],[],[])
			except KeyboardInterrupt:
				print 'Interrupted.'
				return
			else:
				if input in in_set:
					line=input.readline()
					self.sock.sendall(line)
				if self.sock in in_set:
					try: buf=self.sock.recv(self.recv_size)
					except socket.error,e:
						print 'Socket error:',e
					if buf=='':
						print 'Connection closed.'
						return
					output.write(buf)

class TcpSock(NetSock):
	def __init__(self,addr=None,**args):
		if type(addr)==tuple:
			self.sock=socket.socket()
			self.sock.connect(addr)
		for k,v in args.iteritems(): setattr(self,k,v)
		self.recv_size=self.sock.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF)
	def sock_send(self,buf): return self.sock.send(buf)
	def send(self,buf,print_as=None):
		if self.verbose:
			if print_as is None: print_as=repr(buf)
			print 'send(%d): %s'%(len(buf),print_as),
			sys.stdout.flush()
		cnt=self.sock_send(buf)
		if self.verbose: print '%d bytes sent'%(cnt)
		return cnt
	def read(self,*args,**kwargs): return self.recv(*args,**kwargs)
	def write(self,*args,**kwargs): return self.send(*args,**kwargs)
	def has_data(self):
		return self.sock in select.select([self.sock],[],[],0)[0]
	def sock_recv(self,size,nodata_delay):
		if size is None: size=self.recv_size
		try: buf=[self.sock.recv(size)]
		except socket.error,e:
			if e[0] in (errno.ECONNRESET,errno.EBADF): return ''
			else: raise
		if nodata_delay is not None:
			while self.sock in select.select([self.sock],[],[],nodata_delay)[0]:
				data=self.sock.recv(size)
				if data=='': break
				buf.append(data)
		return ''.join(buf)
	def recv(self,size=None,nodata_delay=None):
		data=self.sock_recv(size,nodata_delay)
		if data=='': raise EndOfDataException,"No more data"
		if self.verbose: print 'recv(%d): %r'%(len(data),data)
		return data
	def close(self):
		try: self.sock.shutdown(2)
		except socket.error: pass
		self.sock.close()

class SSLSock(TcpSock):
	def __init__(self,*args,**kwargs):
		TcpSock.__init__(self,*args,**kwargs)
		self.raw_sock=self.sock
		from OpenSSL.SSL import Context,Connection,TLSv1_METHOD
		self.sock=Connection(Context(TLSv1_METHOD),self.raw_sock)
		self.sock.set_connect_state()
		self.sock.do_handshake()
	def sock_recv(self,size,nodata_delay):
		if size is None: size=self.recv_size
		return self.sock.read(size)
	def close(self):
		self.sock.shutdown()
		self.sock=self.raw_sock
		TcpSock.close(self)

class TcpSrvHandler(object):
	def __init__(self,clsock,claddr,**attrs):
		self.sock=TcpSock(sock=clsock)
		self.remote=claddr
		for k,v in attrs.iteritems(): setattr(self,k,v)
	def run(self): raise NotImplementedError,"Need to implement run for %s.%s"%(self.__class__.__module__,self.__class__.__name__)

class SimpleForwarder(object):
	poll_flags=dict([(getattr(select,'POLL%s'%x),x) for x in 'ERR HUP IN MSG NVAL OUT PRI'.split()])
	def __init__(self,clsock,claddr,**args):
		(self.clsock,self.claddr)=clsock,claddr
		self.status="initialized"
		self.rcvlog=[]
		for k,v in args.iteritems(): setattr(self,k,v)
	def process_data(self,data,fd): return data
	def start_loop(self): pass
	def connection_closed(self,fd):
		self.keep_running=False
	def stop_loop(self):
		for sock in map(lambda x: x['sock'],self.socks.itervalues()):
			self.unregister_sock(sock)
	def recv_data(self,fd):
		data=self.socks[fd]['sock'].recv(self.socks[fd]['rcvsize'])
		if data=='': self.connection_closed(fd)
		return data
	def handle_event(self,fd,ev):
		print "%r fd %d POLL%s"%(self.socks[fd]['addr'],fd,flag_str(ev,self.poll_flags))
		if ev&select.POLLERR:
			err=self.socks[fd]['sock'].getsockopt(socket.SOL_SOCKET,socket.SO_ERROR)
			print "%d %s"%(err,errno.errorcode.get(err,"UKN")),
			sys.stdout.flush()
		if ev&select.POLLHUP:
			print "hangup"
			self.keep_running=False
		elif ev&select.POLLIN:
			if debug: print "read data:",
			sys.stdout.flush()
			data=self.recv_data(fd)
			if debug: print repr(data)
			self.rcvlog.append((fd,data))
			data=self.process_data(data,fd)
			if data!='': self.send_data(data,fd)
		else: ValueError,"Unknown event %d"%(ev)
	def send_data(self,data,skip_fd=None):
		for sock in [v['sock'] for k,v in self.socks.iteritems() if k!=skip_fd]:
			sock.sendall(data)
	def poll_loop(self):
		while self.keep_running:
			for fd,ev in self.poll.poll():
				self.handle_event(fd,ev)
	def run(self):
		self.poll=select.poll()
		self.keep_running=True
		self.socks={}
		self.register_sock(self.clsock)
		self.start_loop()
		self.status="running"
		self.poll_loop()
		self.stop_loop()
		self.status="finished"
	def register_sock(self,sock,pollflags=select.POLLIN|select.POLLPRI|select.POLLERR|select.POLLHUP|select.POLLNVAL):
		sockinf={
			'sock':sock,
			'rcvsize':sock.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF),
			'addr':sock.getpeername(),
		}
		self.socks[sock.fileno()]=sockinf
		if pollflags: self.poll.register(sock,pollflags)
	def unregister_sock(self,sock):
		del self.socks[sock.fileno()]
		self.poll.unregister(sock)
		nice_shutdown(sock)

class TcpForwarder(SimpleForwarder):
	def default_remote(self):
		if not getattr(self.server,"tproxy",False):
			print >>sys.stderr,"Server does not have tproxy set, are you sure you don't want to add remote attribute to handler?"
		return self.clsock.getsockname()
	def start_loop(self):
		print "Starting proxy %s<->%s"%(self.claddr,self.remote)
		self.srvsock=socket.socket()
		try: self.srvsock.connect(self.remote)
		except socket.error,e:
			print >>sys.stderr,"Error connecting to %s:"%(self.remote,),e
			raise
		self.register_sock(self.srvsock)
	def __repr__(self):
		return "<%s.%s@%x (%s) %s>"%(self.__class__.__module__,self.__class__.__name__,hash(self)&((sys.maxint<<1)+1),','.join(['%s'%x['addr'] for x in self.socks.itervalues()]),self.status)
proputil.gen_props(TcpForwarder)

class HTTPForwarder(SimpleForwarder):
	remote_re=re.compile(r'^(?P<method>GET|POST) (?P<url>(?:http://(?P<host>.*?)(?::(?P<port>\d+))?)?(?P<path>/.*?)) (?P<http_ver>HTTP/1\.[01])\r\n(?P<headers>(?:[\w-]+: .*?\r\n)*)\r\n',re.S)
	hdr_re=re.compile(r'^(?P<name>[\w-]+): (?P<value>.*?)\r\n',re.M)
	badpath_re=re.compile(r'([\\/:?])')
	replacefile='http_replacements.txt'
	cachedir='http_cache'
	remove_headers=('Keep-Alive','Proxy-Connection','Connection','If-Modified-Since','If-None-Match')
	def __init__(self,*args,**kwargs):
		self.request=None
		self.respfile=None
		SimpleForwarder.__init__(self,*args,**kwargs)
		self.init_replacements(self.replacefile)
	def init_replacements(self,replacefile):
		try: self.replacements
		except AttributeError: self.replacements={}
		try: f=open(replacefile)
		except IOError: pass
		else:
			for k,v in map(lambda x: x.rstrip().split(None,1),f):
				self.replacements[k]=v
	def replace_response(self):
		try: replfile=self.replacements['%s'%(self.request['url'])]
		except KeyError: return False
		if replfile=='!cache':
			replfile=self.get_cachename()	
		try: repl=open(replfile).read()
		except IOError,e:
			print "ERROR: cannot read replacement file %s"%(replfile),e
			return False
		self.send_data(repl)
		self.keep_running=False
		return True
	def get_cachename(self):
		return os.path.join(self.cachedir,self.badpath_re.sub(lambda x: '%%%02x'%(ord(x.group(1))),'%(method)s-%(host)s-%(port)s-%(path)s'%self.request))
	def make_reqstr(self):
		req=self.request
		headers=filter(lambda x: x[0] not in self.remove_headers,[hm.groups() for hm in self.hdr_re.finditer(req['headers'])])
		headers.append(('Connection','close'))
		req['headers_mod']=''.join(['%s: %s\r\n'%x for x in headers])
		print "make_reqstr: %r"%(req,)
		return '%(method)s %(path)s %(http_ver)s\r\n%(headers_mod)s\r\n%(extra)s'%req
	def connect_remote(self):
		self.srvsock=socket.socket()
		try: self.srvsock.connect((self.request['host'],int(self.request['port'])))
		except socket.error,e:
			print >>sys.stderr,"Error connecting to %s:"%(self.remote,),e
			raise
		self.register_sock(self.srvsock)
	def open_savefile(self):
		if os.path.exists(self.cachedir):
			self.respfile=open(self.get_cachename(),'w')
	def process_data(self,data,fd):
		if self.request is None:
			data=''.join([x[1] for x in self.rcvlog if x[0]==fd])
			try: idx=data.index('\r\n\r\n')
			except ValueError: return ''
			match=self.remote_re.match(data)
			req=match.groupdict()
			if req['port'] is None: req['port']=80
			req['extra']=data[match.end():]
			self.request=req
			if self.replace_response(): return ''
			self.connect_remote()
			self.open_savefile()
			return self.make_reqstr()
		if self.respfile is not None and fd!=self.clsock.fileno():
			self.respfile.write(data)
		print "passing thru"
		return data
	def stop_loop(self):
		try: self.unregister_sock(self.srvsock)
		except AttributeError: pass
		if self.respfile is not None: self.respfile.close()

class InteractiveForwarder(TcpForwarder):
	def edit_data(self,data,fd,**add_local):
		l=getattr(self.server,"interactive_locals",{})
		self.server.interactive_locals=l
		l['data']=data
		l['h']=self
		l['fd']=fd
		for k,v in add_local.iteritems(): l[k]=v
		code.interact(banner="Now: data=%r"%(data,),local=l,readfunc=lambda x: raw_input('%s:%d> '%self.remote))
		return l['data']
	def process_data(self,data,fd):
		if raw_input("Edit data?").lower().startswith("y"):
			return self.edit_data(data,fd)
		return data

class PatternInteractiveForwarder(InteractiveForwarder):
	breakon=[]
	def process_data(self,data,fd):
		for pat in self.breakon:
			match=pat.search(data)
			if match is not None:
				return self.edit_data(data,fd,m=match)
		return data

class InteractiveHandler(TcpSrvHandler):
	def fwd(self,data):
		if self.server.tproxy and not hasattr(self,"ss"):
			self.ss=TcpSock(self.sock.getsockname())
		return self.ss.send(data)
	def run(self):
		sock=self.sock
		data=''
		while True:
			if sock.has_data(): data=''.join([data,sock.recv()])
			try: l=self.server.intr_locals
			except AttributeError: l=dict()
			l['data']=data
			l['send']=sock.send
			l['fwd']=self.fwd
			l['h']=self
			if hasattr(self,"ss"): l["ss"]=self.ss
			code.interact(banner="data=%r"%(data),local=l,readfunc=lambda x: raw_input('%s:%d> '%self.remote))
			self.server.intr_locals=l
			data=sock.recv()

class TcpServer(object):
	"""
	Binds to port and spawns hclass with client socket and client address in
	new thread on incoming connection. Sets server attribute on handler to self.

	Attrs:
	  bind_ip,port: address to bind to
	  hargs: additional arguments for hclass's init
	  tproxy: set IP_TRANSPARENT socket option. Needs additional setup:
	    iptables -t mangle -A PREROUTING -j TPROXY -p tcp --on-port $port --tproxy-mark 1 ...
	    ip rule add fwmark 1 lookup 100
	    ip route add local 0.0.0.0/0 dev lo table 100
	"""
	hargs={}
	port=8080
	tproxy=False
	IP_TRANSPARENT=19
	bind_ip="0.0.0.0"
	def __init__(self,hclass,**args):
		self.handlers=[]
		self.old_handlers=[]
		self.hclass=hclass
		self.hlock=thread.allocate_lock()
		for k,v in args.iteritems(): setattr(self,k,v)
	def run_handler(self,clsock,claddr):
		print "connection from %r to %r"%(claddr,clsock.getsockname())
		hargs=dict(self.hargs)
		hargs.setdefault("server",self)
		handler=self.hclass(clsock=clsock,claddr=claddr,**hargs)
		self.hlock.acquire()
		self.handlers.append(handler)
		self.hlock.release()
		try: handler.run()
		except EndOfDataException: pass
		self.hlock.acquire()
		self.handlers.remove(handler)
		self.old_handlers.append(handler)
		self.hlock.release()
		print "shutting down %r"%(claddr,)
		nice_shutdown(clsock)
		clsock.close()
	def create_sock(self):
		self.sock=socket.socket()
		self.sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
		if self.tproxy: self.sock.setsockopt(socket.SOL_IP,self.IP_TRANSPARENT,1)
		print "Binding to %s"%(self.port)
		self.sock.bind((self.bind_ip,self.port))
	def run(self):
		print "handler class:",self.hclass,"args:",self.hargs
		if not hasattr(self, "sock"): self.create_sock()
		self.sock.listen(1)
		self.sock.settimeout(1)
		self.keep_listening=True
		while self.keep_listening:
			try: clsock,claddr=self.sock.accept()
			except socket.timeout: pass
			except KeyboardInterrupt: break
			else: thread.start_new_thread(self.run_handler,(clsock,claddr))
		nice_shutdown(self.sock)
	def stop(self):
		self.keep_listening=False

class SockStreamReader(StreamReader):
	def data_read(self):
		try: return self.stream.read()
		except EndOfDataException: return ""

class TcpStateMachine(FuncSM,DynInit):
	_init_args=('host',)
	sock_debug=False
	timeout=10
	request=""
	def default_ssl_ports(self): return {}
	def default_ssl(self): return True if self.port in self.ssl_ports else False
	def default_reader(self): return SockStreamReader(self.sock)
	def _set_addr(self,value):
		if type(value)==tuple: self.ip,self.port=value
		try: idx=value.index(":")
		except (ValueError,AttributeError): self.ip=value
		else: self.addr=(value[:idx],int(value[idx+1:]))
	def _get_addr(self): return (self.ip,self.port)
	addr=property(_get_addr,_set_addr)
	host=property(lambda self: self.ip,_set_addr)
	def end(self):
		self.sock.close()
	@FuncSM.state(None)
	def start(self):
		if self.ssl: self.sock=SSLSock(self.addr,verbose=self.sock_debug)
		else: self.sock=TcpSock(self.addr,verbose=self.sock_debug)
		if self.timeout is not None: self.sock.settimeout(self.timeout)
		if self.request: self.sock.send(self.request)
proputil.gen_props(TcpStateMachine)

if __name__=="__main__":
	import user #@UnusedImport
	try: port=int(sys.argv[1])
	except IndexError:
		print >>sys.stderr,"Usage: %s <listen_port> [<remoteip>:<port>]"%(sys.argv[0])
		sys.exit(1)
	try: remote=sys.argv[2]
	except IndexError:
		proxy=TcpServer(tproxy=True,hclass=InteractiveForwarder,port=int(sys.argv[1]))
	else:
		remote=remote.split(":")
		proxy=TcpServer(hclass=InteractiveForwarder,port=int(sys.argv[1]),hargs=dict(remote=(remote[0],int(remote[1]))))
	print "proxy: %r"%(proxy,)
	proxy.run()
