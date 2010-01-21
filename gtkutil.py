# 2008-03-28
# * added settimeout to socket in order to fix windows buffering problem

import sys,socket,os
import re
import gtk,gobject
try: import gtk.glade
except ImportError: print >>sys.stderr,"gtk.glade not available"
import cPickle as pickle
import traceback,inspect
import BaseHTTPServer
import StringIO
from misc import flag_str,proputil,dbg

class GladeUI(object):
	def __init__(self,file,cbobj=None):
		self._glade=gtk.glade.XML(file)
		if cbobj!=None: self._glade.signal_autoconnect(cbobj)
	def __getattr__(self,key):
		if key[0]=='_': raise AttributeError,key
		val=self._glade.get_widget(key)
		if val!=None:
			setattr(self,key,val)
			return val
		raise AttributeError,"No '"+key+"' attribute in "+str(self)

class GtkBuilderHelper(object):
	def __init__(self,filename,cbobj=None):
		self.filename=filename
		self._ui=gtk.Builder()
		self._ui.add_from_file(filename)
		if cbobj!=None: self._ui.connect_signals(cbobj)
	def __getattr__(self,key):
		if key[0]=='_': raise AttributeError,key
		val=self._ui.get_object(key)
		if val!=None:
			setattr(self,key,val)
			return val
		raise AttributeError,"No object named '"+key+"' in %r"%(self.filename)

class SimpleBuildGUI(object):
	class Callbacks(object):
		"""Callbacks class definition. Attribute main will hold main instance"""
		def __init__(self,main):
			self.main=main
		def on_quit(self,*args):
			gtk.main_quit() 
	def __init__(self):
		if not hasattr(self,"appname"):	# set appname to a/b/xxx.caller.py -> xxx.caller
			frame=inspect.currentframe()
			self.appname=".".join(os.path.split(frame.f_back.f_code.co_filename)[-1].split(".")[:-1])
		self.cb=self.Callbacks(self)
		self.ui=GtkBuilderHelper(os.path.join(os.path.split(sys.argv[0])[0],'%s.ui'%(self.appname)),self.cb)
	def run(self):
		gtk.main()


class Connectable(object):
	def default_connect_table(self): return {}
	def run_handlers(self,signal,*args,**kwargs):
		for func,add_args,add_kwargs in self.connect_table.get(signal,[]):
			func(self,*(args+add_args),**dict(kwargs,**add_kwargs))
	def connect(self,signal,func,*args,**kwargs):
		self.connect_table.setdefault(signal,[]).append((func,args,kwargs))
proputil.gen_props(Connectable)

class SimpleGUI(object):
	__slots__=[]
	icon=None
	title=None
	size=None
	def __init__(self,**kwargs):
		for k,v in kwargs.items(): setattr(self,k,v)
		self.win=gtk.Window()
		self.win.set_position(gtk.WIN_POS_CENTER)
		if self.title is not None: self.win.set_title(self.title)
		if self.size is not None: self.win.set_size_request(*self.size)
		if self.icon is not None: self.win.set_icon_from_file(self.icon)
		self.win.connect('destroy',gtk.main_quit)
		self.build_ui()
		self.win.show()
	def build_ui(self): pass
	def run(self):
		gtk.main()
	def resize_to_min(self):
		self.win.resize(1,1)
	def set_vis(self,**kwargs):
		for key,vis in kwargs.items():
			o=getattr(self,key)
			if vis: o.show()
			else: o.hide()
		self.resize_to_min()

def in_sw(widget,has_viewport=True):
	"""return widget in scrolled window"""
	sw=gtk.ScrolledWindow()
	sw.set_policy(gtk.POLICY_AUTOMATIC,gtk.POLICY_AUTOMATIC)
	if has_viewport: sw.add(widget)
	else: sw.add_with_viewport(widget)
	return sw

def debug_win(obj,list_internals=True):
	output=""
	infowin=gtk.Window()
	infowin.set_title('Debug win')
	infowin.set_default_size(560,300)
	frame=gtk.Frame(str(obj))
	msg=[]
	dbg(obj,skip_us=not list_internals,writefunc=msg.append)
	frame.add(in_sw(gtk.Label(''.join(msg)),False))
	infowin.add(frame)
	infowin.show_all()

def new_textbuf(view,handle_func=None):
	buf=gtk.TextBuffer()
	if handle_func!=None: buf.connect('changed',handle_func)
	view.set_buffer(buf)
	return buf

class HTTPRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
	clen_re=re.compile(r'^Content-Length: (\d+)[\r\n]',re.I|re.S|re.M)
	def __init__(self,request,client_address,server):
		self.request=request
		self.client_address=client_address
		self.server=server
		self.rfile=StringIO.StringIO()
		self.wfile=StringIO.StringIO()
	def io_in(self,data,sock):
		"""Returns True if has something to send back"""
		curpos=self.rfile.tell()
		self.rfile.seek(0,2)
		self.rfile.write(data)
		self.rfile.seek(0)
		data_in=self.rfile.read()
		self.rfile.seek(curpos)
		end_of_headers=data_in.find('\r\n\r\n')
		if end_of_headers!=-1:
			m=self.clen_re.search(data_in[:end_of_headers])
			if m<>None:
				clen=int(m.group(1))
				if len(data_in[end_of_headers+4:])<clen: return False
			self.handle_one_request()
			self.rfile=StringIO.StringIO(self.rfile.read())
			if self.close_connection:
				if self.rfile.tell()<>0: print 'Warning: Shutting down while we still have data left to read:',repr(self.rfile.getvalue())
				sock.shutdown(0)
			return self.wfile.tell()<>0
	def io_out(self,sock):
		"""Returns True if has more data to send"""
		self.wfile.seek(0)
		self.wfile.seek(sock.send(self.wfile.read()))
		self.wfile=StringIO.StringIO(self.wfile.read())
		if self.wfile.tell()<>0:
			return True
		elif self.close_connection:
			sock.shutdown(1)
		return False
		

class GtkSrv(object):
	cleanup_interval=1000
	cleanup_timeout=10
	io_evs=dict([(getattr(gobject,'IO_'+x),x) for x in 'IN OUT PRI ERR HUP'.split()])
	def __init__(self,port,handler):
		self.port=port
		self.logfile=sys.stderr
		self.handler=handler
		self.clients={}
		gobject.timeout_add(self.cleanup_interval,self.cleanup_clients)
	def on_io_in(self,sock,cond,claddr):
		#print 'on_io_in:',sock,flag_str(cond,self.io_evs),claddr
		data=sock.recv(4096)
		if data=='':			# it means we're disconnected
			#print 'closing connection to',claddr
			self.clients[claddr]['in']=None
			return False
		else:
			if 'timeout' in self.clients[claddr]: del self.clients[claddr]['timeout']
			try:
				if self.clients[claddr]['handler'].io_in(data,sock) and self.clients[claddr]['out']==None:
					self.clients[claddr]['out']=gobject.io_add_watch(sock,gobject.IO_OUT,self.on_io_out,claddr)
			except Exception:
				print 'Error processing data from',claddr,'shutting down:',repr(data)
				sock.shutdown(2)
				raise
		return True
	def cleanup_clients(self):
		#if self.clients: print self.clients
		for claddr,cldata in self.clients.items():
			if 'timeout' in cldata:
				if cldata['timeout']==0:
					print "No data to/from %r in %s seconds, disconnecting"%(claddr,self.cleanup_timeout)
					self.remove_client(claddr)
				else: cldata['timeout']-=1
			else: cldata['timeout']=self.cleanup_timeout
		return True
	def remove_client(self,claddr):
		cldata=self.clients[claddr]
		for handler in ['act','hup','in','out']: 
			if type(cldata[handler])==int: gobject.source_remove(cldata[handler])
		try: cldata['sock'].shutdown(2)
		except socket.error,e:
			if e.args[0]!=107: traceback.print_exc()
		cldata['sock'].close()
		del self.clients[claddr]
	def on_io_act(self,sock,cond,claddr):
		print 'on_io_act:',sock,flag_str(cond,self.io_evs),claddr
		return True
	def on_io_hup(self,sock,cond,claddr):
		self.remove_client(claddr)
		return False
	def on_io_out(self,sock,cond,claddr):
		#print 'on_io_out',sock,flag_str(cond,self.io_evs),claddr
		if 'timeout' in self.clients[claddr]: del self.clients[claddr]['timeout']
		if self.clients[claddr]['handler'].io_out(sock):
			return True
		self.clients[claddr]['out']=None
		return False
	def on_srvsock(self,srvsock,cond):
		(csock,claddr)=srvsock.accept()
		try:
			handler=self.handler(csock,claddr,self)
			self.clients[claddr]={
				'handler':handler,
				'in':gobject.io_add_watch(csock,gobject.IO_IN,self.on_io_in,claddr),
				'out':None,
				'act':gobject.io_add_watch(csock,gobject.IO_PRI|gobject.IO_ERR,self.on_io_act,claddr),
				'hup':gobject.io_add_watch(csock,gobject.IO_HUP,self.on_io_hup,claddr),
				'sock':csock,
			}
		except socket.error,e: traceback.print_exc()
		except Exception,e:
			traceback.print_exc()
			raise e
		return True
	def start(self):
		self.srvsock=socket.socket()
		self.srvsock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,True)
		#self.srvsock.setsockopt(socket.SOL_TCP,socket.TCP_NODELAY,True)
		try: self.srvsock.bind(('',self.port))
		except socket.error,e:
			traceback.print_exc()
			return False
		else:
			self.log("Server started on port",self.port)
			self.srvsock.listen(0)
			self.sock_watch_id=gobject.io_add_watch(self.srvsock,gobject.IO_IN,self.on_srvsock)
		return True
	def stop(self):
		gobject.source_remove(self.sock_watch_id)
		for claddr in self.clients.keys(): self.remove_client(claddr)
		try: self.srvsock.shutdown(2)
		except Exception,e:
			print "error shutting down: %r"%e
		self.srvsock.close()
	def log(self,*args):
		for x in args: print >>self.logfile, x,
		print >>self.logfile

class DataRow:
	def __init__(self,datastore,rowptr=None,data=None):
		self.nameidx=datastore.nameidx
		self.numidx=datastore.numidx
		self.datastore=datastore
		if rowptr==None:
			newrow=[]
			for cdata in self.numidx:
				if cdata['name'] in data: val=data[cdata['name']]
				else: val=cdata['type']()
				newrow.append(self._to_listval(cdata['name'],val))
			rowptr=len(datastore.store)
			datastore.store.append(newrow)
			datastore.is_modified=True
		self.row=datastore.store[rowptr]
	def _from_listval(self,cname,val):
		if self.nameidx[cname]['conv']: return pickle.loads(val)
		else: return val
	def _to_listval(self,cname,val):
		if self.nameidx[cname]['conv']: return pickle.dumps(val)
		else: return val
	def contents(self):
		return dict([(name,self[name]) for name in self.nameidx.keys()])
	def __setitem__(self,cname,val):
		self.row[self.nameidx[cname]['num']]=self._to_listval(cname,val)
		self.datastore.is_modified=True
	def __getitem__(self,cname):
		return self._from_listval(cname,self.row[self.nameidx[cname]['num']])
	def has_key(self,cname):
		return cname in self.nameidx

class DataStore:
	def __init__(self,colstr):
		self.nameidx={}
		self.is_modified=True
		self.numidx=[]
		for cnum,cname,ctype in [(x,y.split(':')[0],eval(y.split(':')[1])) for x,y in enumerate(colstr.split())]:
			cdata=dict(name=cname,conv=not ctype in (str,int,bool),num=cnum,type=ctype)
			self.numidx.append(cdata)
			self.nameidx[cname]=cdata
		self.store=gtk.ListStore(*[x['conv'] and str or x['type'] for x in self.numidx])
	def cnum(self,name): return self.nameidx[name]['num']
	def cname(self,idx): return self.numidx[idx]['name']
	def append(self,newdata):
		newrow=DataRow(self,data=newdata)
		self.is_modified=True
	def set_sort_column_id(self,colname,sort):
		self.store.set_sort_column_id(self.nameidx[colname]['num'],sort)
	def bind_treeview(self,tv,cols):
		tv.set_model(self.store)
		for cname,callback in cols:
			attrs={}
			cnum,ctype=(self.nameidx[cname]['num'],self.nameidx[cname]['type'])
			if ctype==bool:
				rndr=gtk.CellRendererToggle()
				attrs['active']=cnum
				if callback!=None: rndr.connect('toggled',callback,self.store,cnum)
			else:
				rndr=gtk.CellRendererText()
				attrs['text']=cnum
				if callback!=None:
					rndr.set_property('editable',True)
					rndr.connect('edited',callback,self.store,cnum,ctype)
			tvc=tv.insert_column_with_attributes(-1,cname.capitalize(),rndr,**attrs)
			tvc.set_reorderable(True)
			#tvc.set_sort_indicator(True)
		#tv.set_headers_clickable(True)
	def __getitem__(self,rowptr): return DataRow(self,rowptr)
	def __delitem__(self,rowptr):
		del self.store[rowptr]
		self.is_modified=True
	def __setitem__(self,nr,vals):
		row=self[nr]
		for key,val in vals.items(): row[key]=val
		self.is_modified=True
	def _filter_row(self,row,cond):
		for cname,test in cond.items():
			if cname not in self.nameidx or row[self.nameidx[cname]['num']]!=test: return False
		return True
	def select(self,**cond):
		ret=[]
		for nr in [nr for nr,srow in enumerate(self.store) if self._filter_row(srow,cond)]:
			ret.append(self[nr])
		return ret
	def update(self,newdata,**cond):
		for nr,srow in enumerate(self.store):
			if self._filter_row(srow,cond): self[nr]=newdata
		self.is_modified=True
	def contents(self):
		return [x.contents() for x in self.select()]
	def set(self,newdata):
		self.clear()
		for row in newdata: self.append(row)
		self.is_modified=True
	def __len__(self): return len(self.store)
	def clear(self):
		self.store.clear()
		self.is_modified=True
	def __getattr__(self,key):
		raise AttributeError,"No %s in DataStore"%(key)

