import numpy as np
import logging
import ctypes
import os
import time

from ..core import Node, register_node_type, ThreadPollInput
from pyqtgraph.Qt import QtCore, QtGui
from pyqtgraph.util.mutex import Mutex


# http://www.ifnamemain.com/posts/2013/Dec/10/c_structs_python/
# http://scipy-cookbook.readthedocs.io/items/Ctypes.html
# https://stackoverflow.com/questions/2804893/c-dll-export-decorated-mangled-names


"""
Question for blackrock support:
   * possible to get interleave channels ?
   * chunksize (latency) ?
   * channel selection
   * chan_info.smpgroup  ??


"""


#~ p1 = 'C:/Program Files (x86)/Blackrock Microsystems/Cerebus Windows Suite'
#~ p2 = 'C:/Program Files (x86)/Blackrock Microsystems/Cerebus Windows Suite/cbsdk/lib'
p1 = 'C:/Program Files (x86)/Blackrock Microsystems/NeuroPort Windows Suite/cbsdk/lib'
p2 = 'C:/Program Files (x86)/Blackrock Microsystems/NeuroPort Windows Suite'
#~ os.environ['PATH'] = os.path.dirname(__file__) + ';' + os.environ['PATH']
os.environ['PATH'] = p1 + ';' + p2 + ';' + os.environ['PATH']

# TODO make a clas  for DLL load with path.


try:
    dll_cbsdk = ctypes.windll.LoadLibrary('cbsdkx64.dll')
    HAVE_BLACKROCK = True
except :
    dll_cbsdk = None
    HAVE_BLACKROCK = False

print('HAVE_BLACKROCK', HAVE_BLACKROCK)
print(dll_cbsdk)



class CbSdkError( Exception ):
    def __init__(self, errno):
        self.errno = errno
        err_msg = ''
        #~ err_msg = ctypes.create_string_buffer(UL.ERRSTRLEN)
        #~ errno2 = _cbw.cbGetErrMsg(errno,err_msg)
        #~ assert errno2==0, Exception('_cbw.cbGetErrMsg do not work')
        errstr = 'CbSdkError %d: %s'%(errno,err_msg)                
        Exception.__init__(self, errstr)




def decorate_with_error(f):
    def func_with_error(*args):
        errno = f(*args)
        if errno != CBSDKRESULT_SUCCESS:
            raise CbSdkError(errno)
        return errno
    return func_with_error

class CBSDK:
    
    _hack_func_name_to_num = {
        # This is for version 7.0.3 and 6.05.04
        'Open' : 23,
        'Close' : 3,
        'GetChannelConfig' : 5,
        'SetChannelConfig' : 28,
        'SetTrialConfig' : 38,
        'InitTrialData' : 21,
        'GetTrialData' : 17,
    }
    
    def __getattr__(self, attr):
        # this is the normal way
        # f = getattr(dll_cbsdk, 'cbSdk'+attr)
        
        # name of function are mangled because blackrock do no use  exrtern C
        # but with http://www.dependencywalker.com/
        # we can inspect the DLL and "guess" the func
        # here the resul
        
        f = dll_cbsdk[self._hack_func_name_to_num[attr]]
        #~ print(f)
        #~ print('call', attr)
        
        return decorate_with_error(f)


if HAVE_BLACKROCK:
    cbSdk = CBSDK()



class Blackrock(Node):
    """Simple wrapper on top of cbsdk.dll provide by BlackRock micro system.
    To get signal for the CB system.
    """
    
    _output_specs = {
        'aichannels': {}
    }
    
    def __init__(self, **kargs):
        Node.__init__(self, **kargs)
        assert HAVE_BLACKROCK, "Imposible to found DLL: cbsdk.dll"

    def _configure(self, ai_channels=[], nInstance=0, chunksize=4096, apply_config=False):
        """
        ai_channels are blackrock channel 1-based
        
        apply_config: True/False if True it apply config to ai_channels.
                    False use NSP config done by user with "Central" software.
        
        """
        self.ai_channels = ai_channels
        self.nb_channel = len(self.ai_channels)
        #~ self.nb_channel = cbNUM_ANALOG_CHANS
        #~ self.nb_channel = 1
        self.nInstance = nInstance
        
        #~ self.chunksize = cbSdk_CONTINUOUS_DATA_SAMPLES
        self.chunksize = chunksize
        
        self.apply_config = apply_config
        
        
        self.outputs['aichannels'].spec.update({
            'chunksize': chunksize,
            #~ 'shape': (chunksize, self.nb_channel),
            'shape': (-1, self.nb_channel),
            'dtype': 'int16',
            'sample_rate': 30000.,
            'nb_channel': self.nb_channel,
        })
    
    def _initialize(self):
        
        con = cbSdkConnection()
        print(con)
        print(con.szInIP)
        
        print(ctypes.sizeof(con))
        #~ print(con.nInPort, con.szOutIP)
        #~ cbSdk.Open(self.nInstance, CBSDKCONNECTION_DEFAULT)
        #~ cbSdk.Open(self.nInstance, CBSDKCONNECTION_DEFAULT, ctypes.byref(con))
        #~ cbSdk.Open(self.nInstance, CBSDKCONNECTION_DEFAULT, con)
        #~ cbSdk.Open(self.nInstance, CBSDKCONNECTION_CENTRAL)
        cbSdk.Open(self.nInstance, CBSDKCONNECTION_UDP)
        #~ cbSdk.Open(self.nInstance, ctypes.c_int32(CBSDKCONNECTION_UDP), con)
        #~ exit()

        
        self.nb_available_ai_channel = None
        for c in range(cbNUM_ANALOG_CHANS):
            chan_info = cbPKT_CHANINFO()
            try:
                cbSdk.GetChannelConfig(self.nInstance, ctypes.c_short(c+1), ctypes.byref(chan_info))
                print('c', c, 'chan', chan_info.chan, 'chid',chan_info.chid, chan_info.proc, chan_info.bank, 
                            chan_info.label, 'type', chan_info.type,
                            'ainpopts', chan_info.ainpopts, 'smpgroup', chan_info.smpgroup,
                            'ainpcaps', chan_info.ainpcaps)
            except:
                self.nb_available_ai_channel = c
                break
        
        #~ print('nb_available_ai_channel', self.nb_available_ai_channel)
        #~ exit()


        #~ for c in range(cbNUM_ANALOG_CHANS):
            #~ chan_info = cbPKT_CHANINFO()
            #~ cbSdk.GetChannelConfig(self.nInstance, ctypes.c_short(c+1), ctypes.byref(chan_info))
            #~ print('c', c, 'chan', chan_info.chan, chan_info.proc, chan_info.bank, chan_info.label, chan_info.type, chan_info.dlen)
            #~ chan_info.smpfilter = 0 # no filter
            #~ chan_info.smpgroup = 0 # continuous sampling rate (30kHz)
            #~ # chan_info.type = 78 # raw + continuous
            #~ cbSdk.SetChannelConfig(self.nInstance, ctypes.c_short(c+1), ctypes.byref(chan_info))
        
        if self.apply_config:
            # configure channels
            for ai_channel in self.ai_channels:
                chan_info = cbPKT_CHANINFO()
                cbSdk.GetChannelConfig(self.nInstance, ctypes.c_short(ai_channel), ctypes.byref(chan_info))
                chan_info.smpfilter = 0 # no filter
                chan_info.smpgroup = 5 # continuous sampling rate (30kHz)
                chan_info.type = 78
                #~ chan_info.ainpopts = 320
                #~ #cbAINP_RAWSTREAM           0x00000040
                #~ chan_info.ainpopts = 0x00000040
                #~ chan_info.smpgroup = 0 # continuous sampling rate (30kHz)
                #~ chan_info.type = 74
                #~ chan_info.ainpopts = 256
                #~ chan_info.ainpopts = 0x00000040 
                chan_info.ainpopts = 256
                
                cbSdk.SetChannelConfig(self.nInstance, ctypes.c_short(ai_channel), ctypes.byref(chan_info))

#define  cbAINP_LNC_OFF             0x00000000      // Line Noise Cancellation disabled
#define  cbAINP_LNC_RUN_HARD        0x00000001      // Hardware-based LNC running and adapting according to the adaptation const
#define  cbAINP_LNC_RUN_SOFT        0x00000002      // Software-based LNC running and adapting according to the adaptation const
#define  cbAINP_LNC_HOLD            0x00000004      // LNC running, but not adapting
#define  cbAINP_LNC_MASK            0x00000007      // Mask for LNC Flags
#define  cbAINP_REFELEC_LFPSPK      0x00000010      // Apply reference electrode to LFP & Spike
#define  cbAINP_REFELEC_SPK         0x00000020      // Apply reference electrode to Spikes only
#define  cbAINP_REFELEC_MASK        0x00000030      // Mask for Reference Electrode flags
#define  cbAINP_RAWSTREAM_ENABLED   0x00000040      // Raw data stream enabled
#define  cbAINP_OFFSET_CORRECT      0x00000100      // Offset correction mode (0-disabled 1-enabled)
        
        #~ exit()
            
        
        
        #~
        #~ chunksize = 4096
        # configure continuous acq
            #~ CBSDKAPI    cbSdkResult cbSdkSetTrialConfig(UINT32 self.nInstance,
                             #~ UINT32 bActive, UINT16 begchan = 0, UINT32 begmask = 0, UINT32 begval = 0,
                             #~ UINT16 endchan = 0, UINT32 endmask = 0, UINT32 endval = 0, bool bDouble = false,
                             #~ UINT32 uWaveforms = 0, UINT32 uConts = cbSdk_CONTINUOUS_DATA_SAMPLES, UINT32 uEvents = cbSdk_EVENT_DATA_SAMPLES,
                             #~ UINT32 uComments = 0, UINT32 uTrackings = 0, bool bAbsolute = false); // Configure a data collection trial
        cbSdk.SetTrialConfig(self.nInstance, 1, 0, 0, 0, 0, 0, 0, False, 0, cbSdk_CONTINUOUS_DATA_SAMPLES, 0, 0, 0, True)
        #~ cbSdk.SetTrialConfig(self.nInstance, 1, 0, 0, 0, 0, 0, 0, False, 0, self.chunksize, 0, 0, 0, True)
        
        #~ cbSdk.SetTrialConfig(self.nInstance, 1, 1, 0, 0, 4, 0, 0, False, 0, self.chunksize, 0, 0, 0, True)
        
        # create structure to hold the data
        # here contrary to example in CPP I create only one buffer
        # that will be sliced in continuous arrays
        
        self.trialcont = cbSdkTrialCont()
        self.ai_buffer = np.zeros((cbNUM_ANALOG_CHANS, self.chunksize, ), dtype='int16')
        print(self.ai_buffer.shape)
        #~ exit()
        for i in range(cbNUM_ANALOG_CHANS):
            arr = self.ai_buffer[i,: ]
            # self.trial.samples[i] = ctypes.cast(np.ctypeslib.as_ctypes(arr), ctypes.c_void_p)
            #~ print(arr.flags)

            addr, read_only_flag  = arr.__array_interface__['data']
            self.trialcont.samples[i] = ctypes.c_void_p(addr)
            #~ print(self.trial.samples[i])

            
            #~ print(arr.__array_interface__['data'])
            #~ print(type(self.trial.samples[i]))
            #~ print(read_only_flag )
            #~ print(ctypes.c_void_p(addr))
            #~ print(ctypes.cast(addr,  ctypes.c_void_p))
            #~ print(ctypes.cast(np.ctypeslib.as_ctypes(arr), ctypes.c_void_p))
            
        #~ exit()
        
        self.thread = BlackrockThread(self, parent=None)

    def _start(self):
        self.thread.start()

    def _stop(self):
        self.thread.stop()
        self.thread.wait()
        
        

    def _close(self):
        cbSdk.Close(self.nInstance)




class BlackrockThread(QtCore.QThread):
    def __init__(self, node, parent=None):
        QtCore.QThread.__init__(self, parent=parent)
        self.node = node

        self.lock = Mutex()
        self.running = False
        
    def run(self):
        with self.lock:
            self.running = True
        
        
        
        stream = self.node.outputs['aichannels']
        trialcont = self.node.trialcont
        ai_buffer = self.node.ai_buffer
        nInstance = self.node.nInstance
        nb_channel = self.node.nb_channel
        
        #~ chan_select = np.array(ai_channels, dtype=int) - 1
        

            #~ CBSDKAPI    cbSdkResult cbSdkInitTrialData(UINT32 nInstance, UINT32 bActive,
                                       #~ cbSdkTrialEvent * trialevent, cbSdkTrialCont * trialcont,
                                       #~ cbSdkTrialComment * trialcomment, cbSdkTrialTracking * trialtracking);
        
        #~ trialcont.count = 1
        #~ trialcont.chan[0] = 1
        #~ cbSdk.InitTrialData(nInstance, 1, None, ctypes.byref(trialcont), None, None)
        #~ cbSdk.InitTrialData(nInstance, 1, None, ctypes.byref(trialcont), None, None)
        
        n = 0
        next_timestamp = None
        t0 = time.perf_counter()
        while True:
            #~ print('n', n)
            with self.lock:
                if not self.running:
                    break
            
            
            cbSdk.InitTrialData(nInstance, 1, None, ctypes.byref(trialcont), None, None)
            #~ print('INIT trialcont.count', trialcont.count)
            #~ CBSDKAPI    cbSdkResult cbSdkGetTrialData(UINT32 nInstance,
                                          #~ UINT32 bActive, cbSdkTrialEvent * trialevent, cbSdkTrialCont * trialcont,
                                          #~ cbSdkTrialComment * trialcomment, cbSdkTrialTracking * trialtracking);            
            
            print(' trialcont.count',  trialcont.count)
            
            if trialcont.count==0:
                time.sleep(0.003)
                continue
            
            #~ cbSdk.GetTrialData(nInstance, 1, None, ctypes.byref(trialcont), None, None)
            
            num_samples = np.ctypeslib.as_array(trialcont.num_samples)[:nb_channel]
            
            #~ print(num_samples)
            
            if num_samples[0] < 300:
                # it is too early!!!!!!
                print('too early (<300)', num_samples[0])
                print('*'*5)
                time.sleep(0.003)
                continue
            
            print(num_samples)
            
            cbSdk.GetTrialData(nInstance, 1, None, ctypes.byref(trialcont), None, None)
            #~ print(trialcont)
            #~ print('yep')
            #~ if trialcont.count==0:
                #~ continue
            print('trialcont.count', trialcont.count, 'trialcont.time', trialcont.time, 'trialcont.num_samples', trialcont.num_samples[0])
            #~ if trialcont.count==0:
                #~ time.sleep(0.001)
                #~ continue
            t1 = time.perf_counter()
            print((t1-t0)*1000)
            t0 = t1
            
            num_samples = np.ctypeslib.as_array(trialcont.num_samples)[:nb_channel]
            print(num_samples)
            #~ assert np.all(num_samples[0]==num_samples)
            
            num_sample = num_samples[0]
            #~ print('num_sample', num_sample)
            #~ print(ai_buffer.shape)
            
            print(np.ctypeslib.as_array(trialcont.num_samples)[:10])
            print(np.ctypeslib.as_array(trialcont.sample_rates)[:10])
            print(np.ctypeslib.as_array(trialcont.chan)[:10])
            #~ print(ai_buffer[0:10, :20])
            
            
            # since internanlly the memory layout is chanXsample we swap it
            #~ data = ai_buffer.T.copy()
            #~ data = ai_buffer[:, :trialcont.num_samples[0]].T.astype('float32')
            #~ data = ai_buffer[:nb_channel, : trialcont.num_samples[0]].T.copy()
            data = ai_buffer[:nb_channel, : num_sample].T.copy()
            #~ data = ai_buffer[:nb_channel, :300].T.copy()
            #~ data = ai_buffer[: trialcont.num_samples[0], 0].reshape(-1, 1)
            print('data.shape', data.shape)
            #~ print('data.sum', np.sum(data))
            n += data.shape[0]
            stream.send(data, index=n)
            
            if next_timestamp is not None:
                print(next_timestamp, trialcont.time, next_timestamp==trialcont.time)
                pass
            next_timestamp = trialcont.time + num_sample
            
            print('*'*5)
            #~ cbSdk.InitTrialData(nInstance, 1, None, ctypes.byref(trialcont), None, None)
            

    def stop(self):
        with self.lock:
            self.running = False



register_node_type(Blackrock)


# constant and Struct

CBSDKRESULT_SUCCESS = 0
# TODO do this dynamicaly
#cbNUM_ANALOG_CHANS = 256 + 16 # this is version 7.0.x
cbNUM_ANALOG_CHANS = 128 + 16 # this is version 6.5.4
#~ cbNUM_ANALOG_CHANS = 150
cbSdk_CONTINUOUS_DATA_SAMPLES = 102400

CBSDKCONNECTION_DEFAULT = 0 # Try Central then UDP
CBSDKCONNECTION_CENTRAL = 1 # Use Central
CBSDKCONNECTION_UDP = 2 # Use UDP
CBSDKCONNECTION_CLOSED = 3 # Closed
CBSDKCONNECTION_COUNT = 4 # Allways the last value (Unknown)



# for convinient translation
INT32 = ctypes.c_int32
UINT32 = ctypes.c_uint32
INT16 = ctypes.c_int16
UINT16 = ctypes.c_uint16
INT8 = ctypes.c_int8
UINT8 = ctypes.c_uint8
CHAR = ctypes.c_char



class cbSdkConnection(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('nInPort', INT32), # int Client port number
        ('nOutPort', INT32), # int Instrument port number
        ('nRecBufSize', INT32), # int Receive buffer size (0 to ignore altogether)
        ('szInIP', ctypes.c_char_p), # Client IPv4 address
        ('szOutIP', ctypes.c_char_p), # Instrument IPv4 address
    ]
    
    def __init__(self, nInPort=51002,
                       nOutPort=51001,
                       nRecBufSize=4096*2048,
                       szInIP=b"192.168.137.1",
                       szOutIP=b"192.168.137.128"):
        #~ self._szInIP = ctypes.c_char_p(szInIP)
        #~ self._szOutIP = ctypes.c_char_p(szOutIP)
        super().__init__(nInPort, nOutPort, nRecBufSize,
                    szInIP,
                    szOutIP)
                    #~ self._szInIP,
                    #~ self._szOutIP)
                    #~ ctypes.c_char_p(szInIP),
                    #~ ctypes.c_char_p(szOutIP))
        
                    #~ ctypes.byref(ctypes.create_string_buffer(szInIP)),
                    #~ ctypes.byref(ctypes.create_string_buffer(szOutIP)))



class cbSCALING(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('digmin', INT16),
        ('digmax', INT16),
        ('anamin', INT32),
        ('anamax', INT32),
        ('anagain', INT32),
        ('anaunit', CHAR*8),
    ]

class cbFILTDESC(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('label', CHAR*16), 
        ('hpfreq', UINT32), # high-pass corner frequency in milliHertz
        ('hporder', UINT32), # high-pass filter order
        ('hptype', UINT32), # high-pass filter type
        ('lpfreq', UINT32), # low-pass frequency in milliHertz
        ('lporder', UINT32),# low-pass filter order
        ('lptype', UINT32), # low-pass filter type
    ]

class cbMANUALUNITMAPPING(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('nOverride', INT16),
        ('afOrigin', INT16*3),
        ('afShape', (INT16*3)*3),
        ('aPhi', INT16),
        ('bValid', UINT32),
    ]

class cbHOOP(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('valid', UINT16),
        ('time', INT16),
        ('min', INT16),
        ('max', INT16),
    ]


class cbPKT_CHANINFO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ('time', UINT32), # system clock timestamp
        ('chid', UINT16), # 0x8000
        ('type', UINT8), # cbPKTTYPE_AINP*
        ('dlen', UINT8), # cbPKT_DLENCHANINFO
        
        ('chan', UINT32), # actual channel id of the channel being configured
        ('proc', UINT32), # the address of the processor on which the channel resides
        ('bank', UINT32), # the address of the bank on which the channel resides
        ('term', UINT32), # the terminal number of the channel within it's bank
        ('chancaps', UINT32), # general channel capablities (given by cbCHAN_* flags)
        ('doutcaps', UINT32), # digital output capablities (composed of cbDOUT_* flags)
        ('dinpcaps', UINT32), # digital input capablities (composed of cbDINP_* flags)
        ('aoutcaps', UINT32), # analog output capablities (composed of cbAOUT_* flags)
        ('ainpcaps', UINT32), # analog input capablities (composed of cbAINP_* flags)
        ('spkcaps', UINT32), # spike processing capabilities
        ('physcalin', cbSCALING), # physical channel scaling information
        ('phyfiltin', cbFILTDESC), # physical channel filter definition
        ('physcalout', cbSCALING), # physical channel scaling information
        ('phyfiltout', cbFILTDESC), # physical channel filter definition
        ('label', CHAR * 16), # Label of the channel (null terminated if <16 characters)
        ('userflags', UINT32), # User flags for the channel state
        ('position', INT32 * 4), # reserved for future position information
        ('scalin', cbSCALING), # user-defined scaling information for AINP
        ('scalout', cbSCALING), # user-defined scaling information for AOUT
        ('doutopts', UINT32), # digital output options (composed of cbDOUT_* flags)
        ('dinpopts', UINT32), # digital input options (composed of cbDINP_* flags)
        ('aoutopts', UINT32), # analog output options
        ('eopchar', UINT32), # digital input capablities (given by cbDINP_* flags)
        
        # here is in fact a union
        ##('monsource', UINT32), # address of channel to monitor
        ## ('outvalue', INT32), # address of channel to monitor
        ('lowsamples', UINT16), # address of channel to monitor
        ('highsamples', UINT16), # 
        ('offset', INT32), # output value
        
        ('trigtype', UINT8), # trigger type (see cbDOUT_TRIGGER_*)
        ('trigchan', UINT16), # trigger channel
        ('trigval', UINT16), # trigger value
        ('ainpopts', UINT32), # analog input options (composed of cbAINP* flags)
        ('lncrate', UINT32), # line noise cancellation filter adaptation rate
        ('smpfilter', UINT32), # continuous-time pathway filter id
        ('smpgroup', UINT32), # continuous-time pathway sample group
        ('smpdispmin', INT32), # continuous-time pathway display factor
        ('smpdispmax', INT32), # continuous-time pathway display factor
        ('spkfilter', UINT32), # spike pathway filter id
        ('spkdispmax', INT32), # spike pathway display factor
        ('lncdispmax', INT32), # Line Noise pathway display factor
        ('spkopts', UINT32), # spike processing options
        ('spkthrlevel', INT32), # spike threshold level
        ('spkthrlimit', INT32), # 
        ('spkgroup', UINT32), # NTrodeGroup this electrode belongs to - 0 is single unit, non-0 indicates a multi-trode grouping
        ('amplrejpos', INT16), # Amplitude rejection positive value
        ('amplrejneg', INT16), # Amplitude rejection negative value
        ('refelecchan', UINT32), # Software reference electrode channel
        ('unitmapping', cbMANUALUNITMAPPING * 5), # manual unit mapping
        ('spkhoops', (cbHOOP * 4) * 5), # spike hoop sorting set  
    ]

#~ print(ctypes.sizeof(cbPKT_CHANINFO))
#~ exit()

class cbSdkTrialCont(ctypes.Structure):
    _fields_ = [
        ('count', UINT16), # Number of valid channels in this trial (up to cbNUM_ANALOG_CHANS)
        ('chan', (UINT16 * cbNUM_ANALOG_CHANS)), # Channel numbers (1-based)
        ('sample_rates', (UINT16 * cbNUM_ANALOG_CHANS)), # Current sample rate (samples per second)
        ('num_samples', (UINT32 * cbNUM_ANALOG_CHANS)), # Number of samples
        ('time', UINT32), # Start time for trial continuous data
        ('samples', (ctypes.c_void_p * cbNUM_ANALOG_CHANS)), # Buffer to hold sample vectors
    ]

