import sys,os
import thread,re,struct
import socket,select,random

import bz2,gzip
try:
	import Crypto.Hash.MD4
	import Crypto.Hash.MD5
except ImportError:
	print "Crypto lib not installed! Some objects might not be usable."
try:
	import crypt
except ImportError:
	try: import fcrypt as crypt
	except ImportError:
		print "crypt nor fcrypt installed! Some objects might not be usable."

version=(0,4,20091126)

debug=False

def objclsname(obj): return '%s.%s'%(obj.__class__.__module__,obj.__class__.__name__)

class DynInit(object):
	_default_attrs={}
	@classmethod
	def _add_defaults(cls,dst,skip=None):
		if skip is None: skip={}
		for k in cls.__dict__: skip[k]=None
		for k,v in cls._default_attrs.iteritems():
			if k in dst or k in skip: continue
			dst[k]=v.copy() if type(v)==dict else v[:] if type(v)==list else v
		for base in cls.__bases__:
			try: base._add_defaults(dst,skip)
			except AttributeError: pass
		return dst
	def __init__(self,*args,**kwargs):
		for k,v in self._add_defaults(kwargs).iteritems(): setattr(self,k,v)
		for idx,value in enumerate(args): setattr(self,self._init_args[idx],value)

class DynAttr(object):
	def __getattr__(self,key):
		if not key.startswith("get_"):
			setattr(self,key,getattr(self,"get_%s"%key)())
			return getattr(self,key)
		raise AttributeError,"%s.%s has no %r"%(self.__class__.__module__,self.__class__.__name__,key)

class DynAttrClass(object):
	_defaults={}
	_init_tuple=()
	__slots__=['_initkwargs']
	def __init__(self,*args,**kwargs):
		self._initkwargs=kwargs
		for k,v in kwargs.iteritems(): setattr(self,k,v)
		self._initkwargs.clear()
		for idx,arg in enumerate(args): setattr(self,self._init_tuple[idx],arg)
	def _get_set(self,key,val):
		try: setattr(self,key,val)
		except AttributeError: pass
		return val
	def __getattr__(self,key):
		if not key=='_initkwargs' and hasattr(self,'_initkwargs') and key in self._initkwargs:
			return self._get_set(key,self._initkwargs[key])
		if not key.startswith('get_') and hasattr(self,'get_%s'%(key)):
			return self._get_set(key,getattr(self,'get_%s'%(key))())
		if not key=='_defaults' and key in self._defaults:
			return self._get_set(key,self._defaults[key])
		raise AttributeError,"%s has no %r attribute"%(objclsname(self),key)
	def _setattrs(self,**kw):
		for k,v in kw.iteritems(): object.__setattr__(self,k,v)
	def __setattr__(self,key,val):
		if not key.startswith('set_') and hasattr(self,'set_%s'%key):
			return getattr(self,'set_%s'%key)(val)
		return object.__setattr__(self,key,val)

def charrange(start,end):
	return ''.join([chr(x) for x in range(ord(start),ord(end)+1)])

def randomchars(size,alphabet=charrange('a','z')):
	return ''.join([alphabet[random.randint(0,len(alphabet)-1)] for x in range(size)])
	
class CharGen(object):
	__slots__=['alphabet','index','maxlen','maxindex','result_converter']
	def __init__(self,alphabet=charrange('a','z')):
		self.alphabet=alphabet
		self.index=1
		self.maxlen=None
		self.maxindex=None
		self.result_converter=self.join_str
	def __iter__(self):
		while True:
			y=self.get_value_by_index(self.index)
			if self.maxlen is not None and len(y)>self.maxlen: break
			if self.maxindex is not None and self.index>self.maxindex: break
			yield y
			self.index+=1
	def reset(self): self.index=0
	def get_value(self): return self.get_value_by_index(self.index)
	value=property(get_value)
	@staticmethod
	def join_str(val): return ''.join(val)
	def get_value_by_index(self,i):
		if i<1: raise ValueError,"Index must be a positive integer"
		div=i
		ret=[]
		while True:
			div=div-1
			div,mod=divmod(div,len(self.alphabet))
			ret.insert(0,self.alphabet[mod])
			if div==0: break
		return self.result_converter(ret)

class AnsiColors(object):
	colors=dict(
		bold="\x1b[1m",
		red="\x1b[1;31m", 
		green="\x1b[1;32m",
		blue="\x1b[1;34m",
		yellow="\x1b[1;33m",
		off="\x1b[0m", 
		brown="\x1b[33m",
	)
	def __getattr__(self,key):
		return lambda s: "%s%s%s"%(self.colors[key],s,self.colors['off'])

class Password(object):
	def __init__(self,text):
		self.text=text
	def variate_case(self):
		passwd=[x for x in self.text]
		casesens=[(idx,x) for idx,x in enumerate(passwd) if x.lower()!=x.upper()]
		for cycle in xrange(1<<len(casesens)):
			for idx,(pwidx,c) in enumerate(casesens):
				if cycle&(1<<idx): passwd[pwidx]=c.upper()
				else: passwd[pwidx]=c.lower()
			yield Password(''.join(passwd))
	def nthash(self):
		return Crypto.Hash.MD4.new(self.text.encode('utf-16-le')).hexdigest()
	def unixcrypt(self,salt):
		return crypt.crypt(self.text,salt)
	def __str__(self):
		return self.text

class Hash(object):
	def __init__(self,text): self.text=text
	def __str__(self): return self.text
	def is_pass(self,passwd):
		if self.text.startswith('$1$') or len(self.text)==13:
			return self.text==passwd.unixcrypt(self.text)
		elif len(self.text)==65 and self.text[32]==':':
			return self.text[33:]==passwd.nthash()
		else:
			print 'Unknown hash format: %r'%(self.text)

class SpeedTest(object):
	__slots__=['_data','count']
	def __init__(self):
		self.count=10000
	def test1_code(self,offset):
		n=self._data[offset:offset+100]
	def test1(self,data):
		for x in xrange(self.count):
			self._data=data
			self.test1_code(x)
	def test2_code(self,data,offset):
		n=data[offset:offset+100]
	def test2(self,data):
		for x in xrange(self.count):
			self.test2_code(data,x)

def clsname(obj): return '%s.%s'%(obj.__class__.__module__,obj.__class__.__name__)
		
class Test:
	def __getattr__(self,key):
		print '%s.__getattr__: %r'%(clsname(self),key)
		raise AttributeError,"%s has no %r attribute"%(clsname(self),key)
	def test_call(*args):
		print 'XXX: %r'%(args,)
	@classmethod
	def _c(cls,**attrs):
		class ret(cls): pass
		ret.__name__='%s_g'%cls.__name__
		for k,v in attrs.iteritems(): setattr(ret,k,v)
		return ret

class IPV4(object):
	def __getattr__(self,key):
		msg="%r.__getattr__: %r"%(clsname(self),key)
		print >>sys.stderr,msg
		raise AttributeError,msg
	def __init__(self,val):
		if type(val) in (str,unicode): val=struct.unpack('!I',socket.inet_aton(val))[0]
		if type(val) in (int,long): self.val=val
		else: raise TypeError,"Invalid argument type %s for IPV4, need int or str"%(type(val))
	def get_blob(self): return struct.pack('!I',self.val)
	def __str__(self): return socket.inet_ntoa(struct.pack('!I',self.val))
	def __repr__(self): return '<%s %s>'%(clsname(self),self)
	def __int__(self): return self.val
	def __long__(self): return self.val
	def __and__(self,other):
		if type(other)==IPV4: return IPV4(self.val&other.val)
		raise TypeError,"Cannot and IPV4 & %s %r"%(type(other),other)
	def __conform__(self,protocol):
		import pysqlite2.dbapi2
		if protocol is pysqlite2.dbapi2.PrepareProtocol:
			return str(self)
	def rel_ip(self,rel):
		rel_vals=rel.split('.')
		rel_vals.reverse()
		ret=self.val
		for idx,val in enumerate(rel_vals):
			ret=(ret-(ret&(0xff<<(8*idx))))|int(val)<<(8*idx)
		return IPV4(ret)


class LoggableClass(object):
	verbosity=2
	verbosity_levels={0:'ERR',1:'WARN',2:'INFO',3:'DBG'}
	logfile=sys.stdout
	errlog=sys.stderr
	def log(self,msg,level=2):
		if level<2: stream=self.errlog
		else: stream=self.logfile
		if self.verbosity>=level:
			print >>stream,"%s: %s"%(self.verbosity_levels.get(level,'UKN'),msg)
		if hasattr(stream,'flush'):
			stream.flush()

class CompressedFile(LoggableClass):
	def open_gzip(self,filename):
		(fd_in,fd_out)=os.popen2(['zcat',filename])
		fd_in.close()
		return fd_out
	def open_bz2(self,filename): return bz2.BZ2File(filename)
	def __init__(self,filename):
		if filename.endswith('.bz2'): self.fileobj=self.open_bz2(filename)
		elif filename.endswith('.gz'): self.fileobj=self.open_gzip(filename)
	def readline(self): return self.fileobj.readline()
	def close(self): self.fileobj.close()
	def __iter__(self):
		for x in self.fileobj:
			yield x
	# accessing bz2 is ... faster
	def repack_gz_to_bz2(self):
		bzname=self.filename.replace('.gz','.bz2')
		if bzname==self.filename: bzname=self.filename+'.bz2'
		st=os.stat(self.filename)
		if not os.path.exists(bzname) or os.stat(bzname).st_mtime<st.st_mtime:
			os.environ['GZIP_FILE']=self.filename
			os.environ['BZ2_FILE']=bzname
			self.log("Repacking %s to %s"%(self.filename,os.path.basename(bzname)))
			os.system("gzip -dc <${GZIP_FILE} | bzip2 -c >${BZ2_FILE}")
			os.utime(bzname,(st.st_mtime,st.st_mtime))
		return bzname

class URL(object):
	def __init__(self,url_string):
		self.url=url_string

def url_unescape_byte(match,enc):
	if match.group(0)=='+': return ' '
	if match.group('ucode'):
		return struct.pack('H',int(match.group('ucode'),16)).decode('unicode').encode(enc)
	else: return struct.pack('B',int(match.group(1),16))

url_encoded_re=re.compile('\+|%(<?P<val>u(?P<ucode>[0-9a-f]{4})|[0-9a-f]{2})',re.IGNORECASE)
def url_unescape(s,enc):
	"""unescape %uXXXX strings from query"""
	return url_encoded_re.sub(lambda x: url_unescape_byte(x,enc),s)

def flag_str(nr,flagdef={}):
	flags=[]
	for (val,name) in flagdef.items():
		if nr&val:
			flags.append(name)
			nr^=val
	out="|".join(flags)
	if nr!=0: out+="+%d"%(nr)
	return out

class NamedList(object):
	def __init__(self,elements=[]):
		self.elements=elements
		self.values={}
	def set(self,iterable):
		for idx,x in enumerate(iterable):
			self.values[self.elements[idx]]=x
	def get(self):
		return [self.values[x] for x in self.elements]
	def __getitem__(self,key): return self.values[key]
	def __setitem__(self,key,val): self.values[key]=val


class Flags(object):
	def loop_set(self,obj,fmt,flags):
		self.definition=dict([(getattr(obj,fmt%x),x) for x in flags])
	def __init__(self,definition={}):
		self.definition=definition
	def __call__(self,nr):
		flags=[]
		for (val,name) in self.definition.items():
			if nr&val:
				flags.append(name)
				nr^=val
		out="|".join(flags)
		if nr!=0: flags.append("%d"%(nr))
		return "|".join(flags)

def split_dict(orig,keylist):
	ret=dict([(y,orig[y]) for y in [x for x in keylist if x in orig]])
	for key in ret: del orig[key]
	return ret

def extract_args(func,orig_args,skip):
	return split_dict(orig_args,[x for x in func.func_code.co_varnames[:func.func_code.co_argcount] if x not in skip])

def ask_yesno(msg):
	if raw_input(msg).lower().startswith('y'): return True
	return False

def ask_delayed(msg,tm):
	print msg,
	sys.stdout.flush()
	ret=False
	while tm>0:
		sys.stdout.write(str(tm)+"\b"*len(str(tm)))
		sys.stdout.flush()
		if sys.stdin in select.select([sys.stdin],[],[],min(tm,1))[0]:
			ret=True
			break
		tm=tm-1
	if not ret: print "Assuming No."
	return ret

	if raw_input(msg).lower().startswith('y'): return True
	return False

def parse_query(query,enc='ascii',sep='&'):
	ret=[]
	if query==None: return ret
	for keyval in [x.split('=',1) for x in query.split(sep)]:
		for idx,kv in enumerate(keyval): keyval[idx]=url_unescape(kv,enc)
		if keyval[0]!='':
			if len(keyval)==1: keyval.append(None)
			ret.append(tuple(keyval))
	return ret

def map_to_dict(keys,values):
	ret={}
	key_iter=iter(keys)
	val_iter=iter(values)
	while True:
		try: k=key_iter.next()
		except StopIteration: break
		else: ret[k]=val_iter.next()
	return ret

def make_relpath(base,path):
	common=os.path.commonprefix([base,path])
	if base==common and path[len(common)]==os.path.sep: return path[len(common)+1:]
	else: return path

_hexbin=dict([('%x'%idx,x) for idx,x in enumerate(['0000','0001','0010','0011','0100','0101','0110','0111','1000','1001','1010','1011','1100','1101','1110','1111'])])
def int2bin(val):
	return ''.join([_hexbin[x] for x in '%x'%val])

def dbg(obj,skip=[],skip_us=False,writefunc=sys.stdout.write):
	for attr in dir(obj):
		if skip_us and attr[0]=='_': continue
		writefunc('%s'%(attr))
		if attr in skip: writefunc(' *skipped*\n')
		else:
			val=getattr(obj,attr)
			if callable(val):
				code=None
				if hasattr(val,'im_func') and hasattr(val.im_func,'func_code'): code=val.im_func.func_code
				elif hasattr(val,'func_code'): code=val.func_code
				if code<>None: argstr=",".join(code.co_varnames[:code.co_argcount])
				else: argstr="..."
				writefunc("("+argstr+"): "+str(val.__doc__)+"\n")
			else: writefunc(" = "+str(val)+'\n')

class ArgumentableClass(object):
	defaults={}
	__slots__=[]+defaults.keys()
	def __init__(self,**args):
		d=self.defaults.copy()
		d.update(args)
		for k,v in d.iteritems(): setattr(self,k,v)

class HexEd(object):
	__slots__=['data','encoding']
	def __init__(self,fname=None,**args):
		self.encoding='quopri'
		if fname is not None: self.data=open(fname).read()
		for k,v in args.items(): setattr(self,k,v)
	def totext(self,data,offset,end):
		ret=[]
		for c in data[offset:end]:
			if ord(c)<32: c='.'
			elif ord(c)>127: c='.'
			ret.append(c)
		return ''.join(ret)
	def formatted(self,offset=0):
		out=[]
		data=self.data
		addr=0
		step=16
		size=len(data)
		while addr<size:
			hex='  '.join([' '.join(['%02x'%(ord(data[j])) for j in range(i,min(size-1,i+4))]) for i in range(addr,addr+step,4)])
			out.append('%08x   %-50s  %s'%(addr+offset,hex,self.totext(data,addr,addr+step)))
			addr+=step
		return '\n'.join(out)

class proputil:
	@classmethod
	def gen_getter(cls,name):
		def mod_func(get_default_func):
			def getter(self):
				try: return self.__prop_cache[name]
				except AttributeError: self.__prop_cache={}
				except KeyError: pass
				setattr(self,name,get_default_func(self))
				return self.__prop_cache[name]
			return getter
		return mod_func
	@classmethod
	def gen_setter(cls,name):
		def mod_func(clean_func):
			def setter(self,val):
				try: cache=self.__prop_cache
				except AttributeError: cache=self.__prop_cache={}
				cache[name]=clean_func(self,val)
			return setter
		return mod_func
	@classmethod
	def gen_deleter(cls,name):
		def deleter(self):
			try: del self.__prop_cache[name]
			except KeyError: raise AttributeError,"Property %r not in cache"%(name)
		return deleter
	@classmethod
	def gen_props(cls,tgt):
		names=filter(lambda name: name not in tgt.__dict__,dict.fromkeys([x.split("_",1)[1] for x in dir(tgt) if x.startswith("default_") or x.startswith("clean_")]).keys())
		for name in names:
			try: getter=getattr(tgt,"default_%s"%(name))
			except AttributeError:
				def getter(self): raise AttributeError
			try: cleaner=getattr(tgt,"clean_%s"%name)
			except AttributeError: cleaner=lambda self,x: x
			setattr(tgt,name,property(
				cls.gen_getter(name)(getter),
				cls.gen_setter(name)(cleaner),
				cls.gen_deleter(name)))
