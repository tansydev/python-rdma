#!/usr/bin/python
import rdma,rdma.path,sys;
import rdma.IBA as IBA;

class MADError(rdma.RDMAError):
    """Thrown when a MAD transaction returns with an error."""
    def __init__(self,req,rep,**kwargs):
        self.req = req;
        self.rep = rep;
        for k,v in kwargs.iteritems():
            self.__setattr__(k,v);

    def __str__(self):
        if "exc_info" in self.__dict__:
            return repr(self.exc_info);
        # FIXME: Decode what it was we asked for...
        return "MAD error reply status 0x%x - %s"%(self.status,
                                                   IBA.mad_status_to_str(self.status));

class MADTransactor(object):
    """This class is a mixin for everything that implements a MAD transaction
    interface. Derived classes must provide the execute_transaction
    method which sends the MAD and gets the reply.

    By design instances of this interface cannot be multi-threaded. For multi-threaded
    applications each thread must have a separate MADTransactor instance.
    Simple MAD request/reply transactors return payload, other
    attributes for the last processed reply are available via
    instance attributes."""

    reply_path = None;
    """The path for the last reply packet processed"""
    reply_fmt = None;
    """The MADFormat for the last reply packet processed"""

    def __init__(self):
        self._tid = 0;

    def _execute(self,buf,path):
        """Send the fully formed MAD in buf to path and copy the reply
        into buf. Return path of the reply"""
        raise AttributeError(); # ABC

    def _getNewTID(self):
        self._tid = (self._tid + 1) % (1 << 32);
        return self._tid;

    @staticmethod
    def _getMatchKey(buf):
        """Return a tuple that represents the 'key' for MAD buf.
        If two keys match then they are the same transaction."""
        # baseVersion,mgmtClass,classVersion method transactionID[31:0],attributeID
        if isinstance(buf,bytearray):
            return (bytes(buf[0:2]),buf[3],bytes(buf[12:18]));
        return (buf[0:2],ord(buf[3]),buf[12:18]);

    @staticmethod
    def _getReplyMatchKey(buf):
        """Return a tuple that represents the 'key' for response to MAD buf.
        If two keys match then they are the same transaction."""
        # baseVersion,mgmtClass,classVersion method transactionID[31:0],attributeID
        x = MADTransactor._getMatchKey(buf)
        return (x[0],x[1] | IBA.MAD_METHOD_RESPONSE,x[2]);

    def _prepareMad(self,fmt,payload,attributeModifier,method):
        fmt.MADHeader.baseVersion = IBA.MAD_BASE_VERSION;
        fmt.MADHeader.mgmtClass = fmt.MAD_CLASS;
        fmt.MADHeader.classVersion = fmt.MAD_CLASS_VERSION;
        fmt.MADHeader.method = method;
        fmt.MADHeader.transactionID = self._getNewTID();
        fmt.MADHeader.attributeID = payload.MAD_ATTRIBUTE_ID;
        fmt.MADHeader.attributeModifier = attributeModifier;

        # You can pass in the class object for payload and this means
        # send all zeros for it. Used for GET operations with no additional
        # inputs.
        if not isinstance(payload,type):
            payload.pack_into(fmt.data);
        fmt._buf = bytearray(fmt.MAD_LENGTH);
        fmt.pack_into(fmt._buf);

    def _completeMad(self,rbuf,fmt,path,newer,completer):
        # The try wrappers the unpack incase the MAD is busted somehow.
        try:
            if len(rbuf) != fmt.MAD_LENGTH:
                raise MADError(fmt,rbuf,path=path,status=MAD_XSTATUS_INVALID_REPLY_SIZE);
            self.reply_fmt = fmt.__class__(bytes(rbuf));

            # FIXME: Handle BUSY
            # FIXME: Handle redirect
            if self.reply_fmt.MADHeader.status & 0x1F != 0:
                raise MADError(fmt,rbuf,path=path,
                               status=self.reply_fmt.MADHeader.status);
            rpayload = newer(self.reply_fmt.data);
        except MADError:
            raise
        except:
            raise MADError(fmt,rbuf,path=path,exc_info=sys.exc_info());

        if completer:
            return completer(rpayload);
        return rpayload;

    def _doMad(self,fmt,payload,path,attributeModifier,method,completer=None):
        """To support the asynchronous MADTransactor models the RPC wrapper
        caller must always return _doMad(). If for some reason there is some
        post-processing work to do then a completer function must be specified
        to do it."""
        self._prepareMad(fmt,payload,attributeModifier,method);
        rbuf,self.reply_path = self._execute(fmt._buf,path);
        newer = payload if isinstance(payload,type) else payload.__class__;
        return self._completeMad(rbuf,fmt,path,newer,completer);

    def _subnDo(self,payload,path,attributeModifier,method):
        if isinstance(path,rdma.path.IBDRPath):
            fmt = IBA.SMPFormatDirected();
            fmt.drSLID = path.drSLID;
            fmt.drDLID = path.drDLID;
            fmt.initialPath[:len(path.drPath)] = path.drPath;
            fmt.MADHeader.hopCount = len(path.drPath);
        else:
            fmt = IBA.SMPFormat();
        return self._doMad(fmt,payload,path,attributeModifier,method);

    def SubnGet(self,payload,path,attributeModifier=0):
        return self._subnDo(payload,path,attributeModifier,
                           payload.MAD_SUBNGET);
    def SubnSet(self,payload,path,attributeModifier=0):
        return self._subnDo(payload,path,attributeModifier,
                           payload.MAD_SUBNSET);

    def PerformanceGet(self,payload,path,attributeModifier=0):
        return self._doMad(PMFormat(),payload,path,attributeModifier,
                           payload.MAD_PERFORMANCEGET);
    def PerformanceSet(self,payload,path,attributeModifier=0):
        return self._doMad(PMFormat(),payload,path,attributeModifier,
                           payload.MAD_PERFORMANCEGET);

    # TODO ['BMGet', 'BMSet', 'CommMgtGet', 'CommMgtSend', 'CommMgtSet',
    # 'DevMgtGet', 'DevMgtSet', 'SNMPGet',
    # 'SNMPSend', 'SubnAdmDelete', 'SubnAdmGet', 'SubnAdmGetMulti',
    # 'SubnAdmGetTable', 'SubnAdmGetTraceTable', 'SubnAdmSet']
