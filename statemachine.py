#!/usr/bin/python

import re,sys

from misc import DynInit

class SMException(Exception): pass
class EndOfData(SMException): pass
class NoMatch(SMException): pass
class SMFinished(SMException): pass
class WrongMatch(SMException): pass
class DataTimeout(Exception): pass

def dbg(msg):
	print >>sys.stderr,msg

verbose=False
quiet=False

class Match(DynInit):
	_default_attrs=dict(cond=None,start=None,end=None,data=None)
	def __getitem__(self,key):
		if isinstance(self.data,Exception): return self.data[key]
		if key==0: return self.data[self.start:self.end]
		return self.groups[key]
	def __str__(self): return self[0]

class ReMatch(Match):
	def __getitem__(self,key):
		return self.rmatch.group(key)
	def __repr__(self): return "<Re:%r>"%(self.cond.pattern,)

class CondClass(DynInit):
	def match(self,data):
		raise NotImplementedError,"match() needs to be implemented"
	@classmethod
	def listify(cls,tgt):
		if (type(tgt) in (str,unicode) or isinstance(tgt,CondClass)): return [tgt]
		return tgt

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
	def __repr__(self): return "<OnRe:%r>"%(self.pattern,)

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
	def __repr__(self): return "<OnExc:%s>"%(self.exc_type.__name__,)

class ReaderBase(DynInit):
	_default_attrs=dict(skipdata_handler=None,data_log=[],data_buffer=[],old_data=[],textmode=True)
	debug=False
	@staticmethod
	def find_match(data,conditions):
		matchlist=[]
		if verbose: print "Finding match from %r using %r"%(data,conditions)
		for idx,cond in enumerate(conditions):
			try: match=cond.match(data)
			except NoMatch,e: pass
			else:
				match.cond_idx=idx
				matchlist.append(match)
		if not matchlist: raise NoMatch
		matchlist.sort(key=lambda m: m.start)
		return matchlist[0]
	def data_skip(self,data):
		if not quiet and ((not self.textmode) or data.strip()):
			dbg("Skipped data: %r"%data)
	def read_upto(self,conditions):
		condlist=CondClass.listify(conditions)[:]
		for idx,cond in filter(lambda x: type(x[1]) in (str,unicode), enumerate(condlist)):
			condlist[idx]=OnString(cond)
		while True:
			whole_data=''.join(self.data_buffer)
			if whole_data:
				try:
					match=self.find_match(whole_data,condlist)
					if match.start:
						self.data_skip(whole_data[:match.start])
					self.data_buffer=[whole_data[match.end:]]
					return match
				except NoMatch: pass
			try:
				data=self.data_read()
				self.data_log.append(data)
				if self.debug: print "data_read: %r"%(data)
				if data=='': raise EndOfData,"no more data"
			except Exception,e:
				others=OnNever()
				if self.debug and not isinstance(e,EndOfData): print "Got exception while reading data: %r"%(e,)
				try: match=self.find_match(e,map(lambda x: x if isinstance(x,OnException) else others,condlist))
				except NoMatch: raise e
				return match
			self.data_buffer.append(data)
	def data_read(self): raise NotImplementedError,"data_read() needs to be implemented"

class StreamReader(ReaderBase):
	_init_args=('stream',)
	def data_read(self): return self.stream.read()

class FDReader(ReaderBase):
	_init_args=('fd',)
	def data_read(self):
		return os.read(self.fd,8192)

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
				for cond in CondClass.listify(condlist):
					nextconds.append((nextstate,cond))
			self.match=self.reader.read_upto([x[1] for x in nextconds])
			self.prev_state=self.state
			self.state=nextconds[self.match.cond_idx][0]
			if self.debug: print "Matched[%d] %r %r in %r"%(self.match.cond_idx,self.state,self.match,self.match[0])
			self.prev_next_states=self.next_states
			self.next_states=self.execute_handlers(self.state)

class FuncSM(object):
	end_state=None
	debug=False
	@classmethod
	def state(cls,conditions,*next_states):
		"""Decorator for states
		@conditions: list of conditions on which to enter this state
		@next_states...: states which can follow this one"""
		if not next_states: next_states=None
		def mod_func(func):
			func.state_conditions=CondClass.listify(conditions)
			func.next_states=next_states
			return func
		return mod_func
	def run(self):
		self.execute_handlers(self.start)
		self.run_to()
		self.end()
	def execute_handlers(self,state=None):
		if state is not None: self.state=state
		try:
			if type(self.state)==type(self.execute_handlers) and self.state.im_self is self: next_states=self.state()
			else: next_states=self.state(self)
		except SMFinished:
			if self.debug: print "Finished processing at: %s"%(self.state.__name__)
			raise
		if next_states is None: next_states=getattr(self.state,"next_states",None)
		if next_states is not None: self.next_states=next_states
	__sm_need_save_attrs="match prev_state state next_states __saved_state".split()
	def __sm_save_state(self):
		saved_state=object()
		for k in self.__sm_need_save_attrs:
			try: setattr(saved_state,k,getattr(self,k))
			except AttributeError: pass
		return saved_state
	def __sm_restore_state(self,saved_state):
		for k in self.__sm_need_save_attrs:
			try: setattr(self,k,getattr(saved_state,k))
			except AttributeError:
				try: delattr(self,k)
				except AttributeError: pass
	def run_to(self,end=None):
		if end is not None: self.end_state=end
		while self.state:
			if self.debug: print "state: %s -> [%s]"%(self.state.__name__,','.join(['%s'%(x if type(x)==str else x.__name__) for x in self.next_states]))
			conditions=[]
			for nextstate in self.next_states:
				if type(nextstate)==str: nextstate=getattr(self,nextstate)
				for cond in nextstate.state_conditions:
					conditions.append((nextstate,cond))
			self.__saved_state=self.__sm_save_state()
			try: self.match=self.reader.read_upto(map(lambda x: x[1],conditions))
			except EndOfData: break
			self.prev_state=self.state
			self.state=conditions[self.match.cond_idx][0]
			if self.debug: print "Matched[%d] %s: %r in %r"%(self.match.cond_idx,self.state.__name__,self.match,self.match[0])
			try: self.execute_handlers()
			except SMFinished: break
			except WrongMatch: self.__sm_restore_state(self.__saved_state)
			if self.state==self.end_state: break
	def end(self): pass
