#!/usr/bin/python

import re,sys

class SMException(Exception): pass
class EndOfData(SMException): pass
class NoMatch(SMException): pass

def dbg(msg):
	print >>sys.stderr,msg

verbose=False
quiet=False

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

class Match(DynInit):
	_default_attrs=dict(cond=None,start=None,end=None,data=None)
	def __getitem__(self,key):
		if key==0: return self.data[self.start:self.end]
		return self.groups[key]
	def __str__(self): return self[0]

class ReMatch(Match):
	def __getitem__(self,key):
		return self.rmatch.group(key)
	def __repr__(self): return "<%s.%s@%x %r>"%(self.__class__.__module__,self.__class__.__name__,((1<<32)-1)&hash(self),self.cond.pattern)

class CondClass(DynInit):
	def match(self,data):
		raise NotImplementedError,"match() needs to be implemented"

class OnNever(CondClass):
	def match(self,data): raise NoMatch

class OnRegex(CondClass):
	_init_args=('regex','flags')
	_default_attrs={'flags':re.I|re.M|re.S,'pattern':None}
	def __init__(self,*args,**kwargs):
		DynInit.__init__(self,*args,**kwargs)
		if type(self.regex) in (str,unicode):
			self.pattern=self.regex
			self.regex=re.compile(self.pattern,self.flags)
	def match(self,data):
		rmatch=self.regex.search(data)
		if rmatch is None: raise NoMatch
		return ReMatch(cond=self,start=rmatch.start(),end=rmatch.end(),data=data,rmatch=rmatch)
	def __repr__(self): return "<%s.%s %r>"%(self.__class__.__module__,self.__class__.__name__,self.pattern)

class OnString(CondClass):
	_init_args=('check',)
	def match(self,data):
		try: idx=data.index(self.check)
		except ValueError: raise NoMatch
		return Match(cond=self,start=idx,end=idx+len(self.check),data=data)

class OnException(CondClass):
	_init_args=("exc_type",)
	def match(self,data):
		if isinstance(data,self.exc_type): return Match(data=data)
		raise NoMatch

class ReaderBase(DynInit):
	_default_attrs=dict(data_buffer=[],old_data=[],textmode=True)
	@staticmethod
	def find_match(data,conditions):
		if verbose: print "Finding match from %r using %r"%(data,conditions)
		for idx,cond in enumerate(conditions):
			try: match=cond.match(data)
			except NoMatch,e: pass
			else:
				match.cond_idx=idx
				return match
		raise NoMatch
	def read_upto(self,conditions):
		if type(conditions) in (str,unicode) or isinstance(conditions,CondClass):
			conditions=[conditions]
		condlist=conditions[:]
		for idx,cond in filter(lambda x: type(x[1]) in (str,unicode), enumerate(condlist)):
			condlist[idx]=OnString(cond)
		while True:
			whole_data=''.join(self.data_buffer)
			if whole_data:
				try:
					match=self.find_match(whole_data,condlist)
					if match.start and not quiet and ((not self.textmode) or whole_data[:match.start].strip()):
						dbg("Skipped data: %r"%whole_data[:match.start])
					self.data_buffer=[whole_data[match.end:]]
					return match
				except NoMatch: pass
			try:
				data=self.data_read()
				if data=='': raise EndOfData
			except Exception,e:
				others=OnNever()
				print "Got exception while reading data: %r"%(e,)
				match=self.find_match(e,map(lambda x: x if isinstance(x,OnException) else others,condlist))
				if match: return match
				else: raise
			self.data_buffer.append(data)
	def data_read(self): raise NotImplementedError,"data_read() needs to be implemented"

class StreamReader(ReaderBase):
	_init_args=('stream',)
	def data_read(self): return self.stream.read()

class StateMachine(DynInit):
	_default_attrs=dict(states={'start':(["end"],),'end':(None,OnException(EndOfData))},state="start",next_states=None,prev_next_states=None,debug=False,log=[])
	_init_args=('reader',)
	def execute_handlers(self,state):
		try: handler=getattr(self,"on_%s"%(state))
		except AttributeError: pass
		else: return handler()
	def on_start(self): pass
	def run(self):
		self.on_start()
		self.goto("end")
	def goto(self,end="end"):
		while self.state!=end:
			nextconds=[]
			if self.next_states is None:
				self.next_states=self.states[self.state][0]
			if self.next_states is None: self.next_states=self.prev_next_states
			if self.debug: print "goto state: %r -> %r"%(self.state,self.next_states)
			for nextstate in self.next_states:
				try: condlist=self.states[nextstate][1]
				except IndexError: raise ValueError,"No conditions defined for %r"%(nextstate,)
				if type(condlist) in (str,unicode) or isinstance(condlist,CondClass): condlist=[condlist]
				for cond in condlist:
					nextconds.append((nextstate,cond))
			self.match=self.reader.read_upto([x[1] for x in nextconds])
			self.prev_state=self.state
			self.state=nextconds[self.match.cond_idx][0]
			if self.debug: print "Match[%d]: %r %r -> %r"%(self.match.cond_idx,self.state,self.match[0],self.match)
			self.prev_next_states=self.next_states
			self.next_states=self.execute_handlers(self.state)
