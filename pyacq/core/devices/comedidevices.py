# -*- coding: utf-8 -*-

import multiprocessing as mp
import numpy as np
import msgpack
import time
import resource

from collections import OrderedDict

import zmq


from pycomedi.device import Device
from pycomedi.subdevice import StreamingSubdevice
from pycomedi.channel import AnalogChannel
from pycomedi.chanspec import ChanSpec
from pycomedi.constant import (AREF, CMDF, INSN, SUBDEVICE_TYPE, TRIG_SRC, UNIT)
from pycomedi.utility import inttrig_insn, Reader, Writer, MMapReader
from pycomedi import PyComediError
from pycomedi.calibration import CalibratedConverter

import mmap

from .base import DeviceBase


def prepare_device(dev, ai_channel_indexes, ai_channel_ranges, sampling_rate):
    nb_ai_channel = len(ai_channel_indexes)
    #~ dev.parse_calibration()
    
    ai_subdevice = dev.find_subdevice_by_type(SUBDEVICE_TYPE.ai, factory=StreamingSubdevice)
    #~ aref = AREF.diff
    #~ aref = AREF.ground
    aref = AREF.common
    
    ai_channels = [ ai_subdevice.channel(int(i), factory=AnalogChannel, aref=aref) for i in ai_channel_indexes]
    for chan, range_ in zip(ai_channels, ai_channel_ranges):
        chan.range = chan.find_range(unit=UNIT.volt, min=range_[0], max=range_[1])
    
    dt = ai_subdevice.get_dtype()
    itemsize = np.dtype(dt).itemsize
    
    # need to align to mmap page size
    resource.getpagesize()
    internal_size = int(ai_subdevice.get_max_buffer_size()//nb_ai_channel//itemsize//resource.getpagesize())*resource.getpagesize()
    #~ print 'internal_size', internal_size
    ai_subdevice.set_buffer_size(internal_size*nb_ai_channel*itemsize)
    #~ print 'buffersize', internal_size*nb_ai_channel*itemsize
    
    # make comedi comand
    scan_period_ns = int(1e9 / sampling_rate)
    ai_cmd = ai_subdevice.get_cmd_generic_timed(len(ai_channels), scan_period_ns)
    ai_cmd.chanlist = ai_channels
    ai_cmd.start_src = TRIG_SRC.now
    ai_cmd.start_arg = 0
    ai_cmd.stop_src = TRIG_SRC.none
    ai_cmd.stop_arg = 0
    ai_subdevice.cmd = ai_cmd
    
    # test cmd
    for i in range(3):
        rc = ai_subdevice.command_test()
        if rc is not None:
            print 'Not able to command_test properly'
            return
    
    return ai_subdevice,  ai_channels, internal_size


def device_mainLoop(stop_flag, streams, device_path, device_info, ai_channel_ranges ):
    streamAD = streams[0]
    
    packet_size = streamAD['packet_size']
    sampling_rate = streamAD['sampling_rate']
    arr_ad = streamAD['shared_array'].to_numpy_array()
    ai_channel_indexes = streamAD['channel_indexes']
    
    nb_ai_channel = streamAD['nb_channel']
    half_size = arr_ad.shape[1]/2
    
    context = zmq.Context()
    socketAD = context.socket(zmq.PUB)
    socketAD.bind("tcp://*:{}".format(streamAD['port']))

    dev = Device(device_path)
    dev.open()
    ai_subdevice,  ai_channels, internal_size = prepare_device(dev, ai_channel_indexes, ai_channel_ranges,  sampling_rate)
    
    dt = ai_subdevice.get_dtype()
    itemsize = np.dtype(dt).itemsize
    
    #~ try:
        #~ dev.parse_calibration()
        #~ for chan in ai_channels:
            #~ print chan.index, chan.range, chan.get_converter()
        #~ converters = [c.get_converter() for c in ai_channels]
        #~ print 'with comedi calibrate'
    #~ except PyComediError as e:
    if 1:
        # if comedi calibrate not work we put manual pylynom
        converters = [ ]
        for chan in ai_channels:
            phys_range = float(chan.range.max - chan.range.min)
            logic_range = np.iinfo(dt).max-np.iinfo(dt).min
            conv = CalibratedConverter(to_physical_coefficients=[-phys_range/logic_range*2., phys_range/logic_range,0.,0.],
                                        to_physical_expansion_origin=logic_range//2-1,
                                        )
            converters.append(conv)
        print 'manual callibration with linear polynom'

    
    ai_buffer = np.memmap(dev.file, dtype = dt, mode = 'r', shape = (internal_size, nb_ai_channel))
    ai_subdevice.command()
    
    pos = abs_pos = 0
    last_index = 0
    socketAD.send(msgpack.dumps(abs_pos))
    
    sleep_time = 0.01
    while True:
        try:
        #~ if 1:
            new_bytes =  ai_subdevice.get_buffer_contents()
            remaining_bytes = new_bytes%(nb_ai_channel*itemsize)
            new_bytes = new_bytes - remaining_bytes
            
            index = (last_index + new_bytes//nb_ai_channel//itemsize)%internal_size
            
            if index == last_index : 
                time.sleep(sleep_time)
                continue
            
            if index< last_index:
                new_samp = internal_size - last_index
                new_samp2 = min(new_samp, arr_ad.shape[1]-(pos+half_size))
                for i,c in enumerate(converters):
                    arr_ad[i,pos:pos+new_samp] = c.to_physical(ai_buffer[ last_index:internal_size, i ])
                    arr_ad[i,pos+half_size:pos+new_samp2+half_size] = arr_ad[i,pos:pos+new_samp2]
                
                last_index = 0
                abs_pos += int(new_samp)
                pos = abs_pos%half_size

            new_samp = index - last_index
            new_samp2 = min(new_samp, arr_ad.shape[1]-(pos+half_size))
            
            #Analog
            for i,c in enumerate(converters):
                arr_ad[i,pos:pos+new_samp] = c.to_physical(ai_buffer[ last_index:index, i ])
                arr_ad[i,pos+half_size:pos+new_samp2+half_size] = arr_ad[i,pos:pos+new_samp2]
            
            abs_pos += int(new_samp)
            pos = abs_pos%half_size
            last_index = index%internal_size
            
            socketAD.send(msgpack.dumps(abs_pos))
            
            ai_subdevice.mark_buffer_read(new_bytes)
            
        except :
            print 'Problem in acquisition loop'
            break
            
        if stop_flag.value:
            print 'should stop properly'
            break
        
        
    try:
        dev.close()
        print 'has stop properly'
    except :
        print 'not able to stop cbStopBackground properly'


def create_analog_subdevice_param(n):
    d = {
                'type' : 'AnalogInput',
                'nb_channel' : n,
                'params' :{  }, 
                'by_channel_params' : { 
                                        'channel_indexes' : range(n),
                                        'channel_names' : [ 'AI Channel {}'.format(i) for i in range(n)],
                                        'channel_selection' : [True]*n,
                                        'channel_ranges' : [ [-10., 10.] for i in range(n) ],
                                        }
            }
    return d

def get_info(device_path):
    info = { }
    dev = Device(device_path)
    dev.open()    
    info['class'] = 'ComediMultiSignals'
    info['device_path'] = device_path
    info['board_name'] = dev.get_board_name()
    info['global_params'] = {
                                            'sampling_rate' : 4000.,
                                            'buffer_length' : 60.,
                                            }
    info['subdevices'] = [ ]
    for sub in dev.subdevices():
        if sub.get_type() == SUBDEVICE_TYPE.ai:
            n = sub.get_n_channels()
            info_sub = create_analog_subdevice_param(n)
            info['subdevices'].append(info_sub)
        #~ elif sub.get_type() ==  SUBDEVICE_TYPE.di:
    
    info['device_packet_size'] = 512
    dev.close()
    return info

class ComediMultiSignals(DeviceBase):
    """
    Usage:
        dev = ComediMultiSignals()
        dev.configure(...)
        dev.initialize()
        dev.start()
        dev.stop()
        
    Configuration Parameters:
        nb_channel
        sampling_rate
        buffer_length
        packet_size
        channel_names
        channel_indexes
    
    
    """
    def __init__(self,  **kargs):
        DeviceBase.__init__(self, **kargs)
    
    @classmethod
    def get_available_devices(cls):
        devices = OrderedDict()
        i = 0
        while True:
            device_path = '/dev/comedi{}'.format(i)
            try:
                info = get_info(device_path)
                devices[info['board_name']+str(i)] = info
            except PyComediError:
                break
            i += 1
        return devices

    def configure(self,
                                    device_path = '/dev/comedi0',
                                    buffer_length= 10.,
                                    sampling_rate =1000.,
                                    subdevices = None,
                                    ):
        self.params = {'device_path' : device_path,
                                'buffer_length' : buffer_length,
                                'sampling_rate' : sampling_rate,
                                'subdevices' : subdevices,
                                }
        self.__dict__.update(self.params)
        self.configured = True

    def initialize(self, streamhandler = None):
        info = self.device_info = get_info(self.device_path)
        if self.subdevices is None:
            self.subdevices = info['subdevices']
        
        sub0 = self.subdevices[0]
        assert sub0['type'] == 'AnalogInput', 'deal only with AnalogInput at the moment'
        sel = sub0['by_channel_params']['channel_selection']
        self.nb_channel = np.sum(sel)
        channel_indexes = [e   for e, s in zip(sub0['by_channel_params']['channel_indexes'], sel) if s]
        channel_names = [e  for e, s in zip(sub0['by_channel_params']['channel_names'], sel) if s]
        self.ai_channel_ranges = [e  for e, s in zip(sub0['by_channel_params']['channel_ranges'], sel) if s]
        self.packet_size = int(info['device_packet_size']/self.nb_channel)
        
        # compute the real sampling rate
        print 'sampling_rate wanted:', self.sampling_rate
        dev = Device(self.device_path)
        dev.open()
        ai_subdevice,  ai_channels, internal_size = prepare_device(dev, channel_indexes, self.ai_channel_ranges,  self.sampling_rate)
        self.sampling_rate = 1.e9/ai_subdevice.cmd.convert_arg/len(ai_channels)
        dev.close()
        print 'sampling_rate real:', self.sampling_rate
        
        
        
        l = int(self.sampling_rate*self.buffer_length)
        #~ print l, l - l%self.packet_size, (l - l%self.packet_size)/self.sampling_rate
        self.buffer_length = (l - l%self.packet_size)/self.sampling_rate
        self.name = '{} #{}'.format(info['board_name'], info['device_path'].replace('/dev/comedi', ''))
        s  = self.streamhandler.new_AnalogSignalSharedMemStream(name = self.name+' Analog', sampling_rate = self.sampling_rate,
                                                        nb_channel = self.nb_channel, buffer_length = self.buffer_length,
                                                        packet_size = self.packet_size, dtype = np.float64,
                                                        channel_names = channel_names, channel_indexes = channel_indexes,            
                                                        )
        
        
        
        self.streams = [s, ]

    
    def start(self):
        self.stop_flag = mp.Value('i', 0)
        
        self.process = mp.Process(target = device_mainLoop,  args=(self.stop_flag, self.streams, self.device_path, self.device_info, self.ai_channel_ranges) )
        self.process.start()
        
        print 'ComediMultiSignals started:', self.name
        self.running = True
    
    def stop(self):
        self.stop_flag.value = 1
        self.process.join()
        print 'ComediMultiSignals stopped:', self.name
        
        self.running = False
    
    def close(self):
        pass
        #TODO release stream and close the device


        
        