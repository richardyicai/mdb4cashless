#! /usr/bin/python
# coding=utf-8

import serial
import time
import math
# import threading
# import queue


CASHLESS_STATE_INACTIVE = 1
CASHLESS_STATE_DISABLED = 2
CASHLESS_STATE_ENABLED = 3
CASHLESS_STATE_SESSION_IDLE = 4
CASHLESS_STATE_VEND = 5
CASHLESS_STATE_REVALUE = 6
CASHLESS_STATE_NEGATIVE = 7

CASHLESS_DEVICE1_ADDRESS = 0x10
CASHLESS_DEVICE2_ADDRESS = 0x60

CASHLESS_DEVICE_INFO = {0x00:"Just Reset",             0x01:"Reader Config Data",
                     0x02:"Display Request",        0x03:"Begin Session",
                     0x04:"Session Cancel Request", 0x05:"Vend Approved",
                     0x06:"Vend Denied",            0x07:"End Session",
                     0x08:"Cancelled",              0x09:"Peripheral",
                     0x0A:"Malfunction/Error",      0x0B:"Cmd Out Of Sequence",
                     0x0D:"Revalue Approved",        0x0E:"Revalue Denied",
                     0x0F:"Revalue Limit Amount",   0x10:"User File Data",
                     0x11:"Time/Data request",      0x12:"Data Entry Request",
                     0x13:"Data Entry Cancel",      0x1B:"FTL REQ TO RCV",
                     0x1C:"FTL RETRY/DENY",         0x1D:"FTL SEND BLOCK",
                     0x1E:"FTL OK TO SEND",         0x1F:"FTL REQ TO SEND",
                     0xFF:"Diagnostic Response"
}

'''
FUCKING WAFER adapter:
不用POLL指令
MDB 盒子发送给电脑的数据是按照 HEX 方式(16 进制方式)，第一个字节是设备的 ID

0x10 (reset) return:  b'00 \r\n10 00\r\n'
0x11-00 configure data : b'01 02 00 9C 01 01 07 0D B5 \r\n'
0x12 (poll) return: b'00 \r\n'

vend request 回复： b'00 \r\n10 05 00 0A\r\n'
vend request 超时： b'10 04\r\n'

when user swiped card : b'10 03 07 D0 33 31 31 36 00 00 00\r\n'

reader cancel: b'00 \r\n10 08\r\n'

any error return: b'FF \r\n'



LEVEL 1 :

INIT PROCESS:

[0x10]  REST            b'00 \r\n10 00\r\n'  
[0x11, 0x00, 0x03, 0x00, 0x00, 0x00] CONFIG   b'01 01 00 9C 01 02 07 0D B5 \r\n'
[0x11, 0x01, 0xFF, 0xFF, 0, 0] SET PRICE    b'00 \r\n'
[0x14, 0x01] ENABLE    b'00 \r\n'


READ CARD
b'10 03 03 E8\r\n'

[0x13, 0x0 , 0x0,  0x01, 0xFF, 0xFF]  VEND REQUEST (SELECT ITEM)   b'00 \r\n10 05 00 01\r\n'

[0x13, 0x02]  SUCCESS  

[0x13, 0x03]  FAILURE b'00 \r\n'

[0x13, 0x04]  SESSION COMPLETE  b'00 \r\n10 07\r\n'


'''

ACK = b'00 \r\n'
NACK = b'FF \r\n'

class MDBException(RuntimeError):
    def __init__(self, msg):
        self.msg_ = msg

class MDBTimeout(MDBException):
    def __init__(self, msg):
        self.msg_ = msg

class MDBNake(MDBException):
    def __init__(self, msg):
        self.msg_ = msg

class MDBProtocol(MDBException):
    def __init__(self, msg):
        self.msg_ = msg

class MDBSequence(MDBException):
    def __init__(self, msg):
        self.msg_ = msg

class MDBRequestDeny(MDBException):
    def __init__(self, msg):
        self.msg_ = msg

class MdbJob():
    def __init__(self, cmd, callback):
        self._cmd = cmd
        self._callback = callback


class Config():

    def __init__(self, level, country_code, scale_factor, decimal_places, amrt, misc):
        self._level = level
        self._country_code = country_code
        self._scale_factor = scale_factor
        self._decimal_places = decimal_places
        self._amrt = amrt
        self._misc = misc


    def __repr__(self):
        return "reader config:\r\nlevel: {}, " \
               "country_code: {}, " \
               "scale_factor: {}, " \
               "decimal_places: {}, " \
               "application maximum response time: {}, options: (refund: {}, multivend: {}, display: {}, cash:{})".format(
            self._level,
            self._country_code,
            self._scale_factor,
            self._decimal_places,
            self._amrt,
            self._misc&0x01, self._misc&0x02, self._misc&0x04, self._misc&0x08
        )

class SessionInfo():
    def __init__(self, funds, id, ptype, pdata):
        self._funds = funds
        self._id= id
        self._ptype = ptype
        self._pdata = pdata

    def __repr__(self):
        return "SessionInfo:\r\n" \
               "funds: {}," \
               "id: {}," \
               "type: {}," \
               "data:{}".format(self._funds, self._id, self._ptype, self._pdata)

class MDBCashless():
    _always_idle = False

    def __init__(self, ser = serial.Serial(), level = 1):
        self.ser = ser
        if self.ser:
            self.ser.flush()
        self.st = CASHLESS_STATE_INACTIVE
        self.level = level

    def _set_state(self, st):
        self.st = st

    def get_state(self):
        return self.st

    def set_level(self, level):
        self.level = level

    @property
    def always_idle(self):
        if self._always_idle and self._always_idle == True:
            return True
        return False

    @always_idle.setter
    def always_idle(self, enabled):
        self._always_idle = enabled


    def _parse_result(self, cmd):
        print("_parse_result({})".format(cmd))
        if  cmd.find(NACK) == 0 :
            return (False, [])

        if cmd.find(ACK) ==0:
            cmd = cmd[len(ACK):]

        if len(cmd)==0:
            return (True, [])

        idx = cmd.find(b'\r\n')
        if idx < 0:
            raise MDBException("parse error")
        cmd = cmd[:idx].strip(b' ')

        #rd =cmd[:idx].decode('utf-8').split(" ")
        #print("splited return msg: {}".format(cmd[:idx].decode('utf-8').split(" ")))
        return (True, [int(X,16) for X in cmd[:idx].decode('utf-8').split(" ")])


    def _print_cmd_sequence(self, cmd, success, result, level=1):
        for i in range(level):
            print(" ", end="")
        print("cmd: ", end="")
        print(" [",end="")
        for c in cmd:
            print(" {0:02X} ".format(c),end="")

        print("] ",end="" )

        print("result: (success: {:02x}) ".format(success), end="")
        print(" [", end="")
        for c in result:
            print(" {0:02X} ".format(c), end="")

        print("]")


    def _print_msg(self, msg):
        print(msg)

    def get_result(self, timeout=5):
        end = time.time() + timeout
        result_bstr = b''
        time.sleep(0.1)
        while time.time() < end:
            r=self.ser.read_all()
            if r:
                result_bstr=result_bstr+r
            else:
                time.sleep(1)
                continue
            if result_bstr.find(b'\r\n') > 0:
                if result_bstr[-2:]!=b'\r\n':
                    raise MDBException("message error may be multiple msg: {}".format(result_bstr))
                return result_bstr
        raise MDBException("timeout when receiving msg: {}".format(result_bstr))


    def get_one_message(self, timeout):
        tout = self.ser.timeout
        self.ser.timeout=timeout
        result_bstr = b''
        TM = b"\r\n"
        while True:
            result_bstr = self.ser.read_until(TM)
            print("msg return: {}".format(result_bstr))
            if not result_bstr or result_bstr[-len(TM):]!=TM:
                self.ser.timeout=tout
                raise MDBTimeout("read TM timeout")
            else:
                self.ser.timeout=tout
                return result_bstr


    def get_poll_message(self,timeout=10):
        ret = self.get_one_message(timeout = timeout)
        if ret.find(b' \r\n') >= 0:
            ret = ret[:-3]
        else:
            ret = ret[:-2]

        return [int(X, 16) for X in ret.decode('utf-8').split(" ")]



    def do_cmd(self, addr, cmd,adapter_response = True ,mdb_response=False, poll = False , timeout=10):
        self.ser.flush() # TODO
        #wait for adapter response, ACK, NACK or no response(need retry)
        RETRY = 3
        ret = ''
        ack = False
        cmd[0] = cmd[0] + addr
        for i in range(RETRY):
            try:

                print('send cmd: {}'.format(bytes(cmd)))
                self.ser.write(cmd)
                time.sleep(0.5)
                if not adapter_response:
                    break
                ret=self.get_one_message(4)

                print("ACK/NACK: {}".format(ret))
                if ret == b'00 \r\n':
                    ack = True
                    break
                elif ret == b'FF \r\n':
                    time.sleep(0.2)

            except MDBTimeout as ex:
                if i == RETRY-1:
                    raise ex

        if adapter_response and not ack:
            raise MDBNake("adapter reply: {}".format(ret))

        ret = b''
        response = []

        if mdb_response:
            ret = self.get_one_message(5)
            print("mdb_response: {}".format(ret))
            print("poll reply: {}".format(ret))
            if ret.find(b' \r\n') >= 0:
                ret = ret[:-3]
            else:
                ret = ret[:-2]

            response = [ int(X, 16) for X in ret.decode('utf-8').split(" ")]

        for i in range(3):
            ret = b''
            poll_reply=[]
            if poll:
                ret = self.get_one_message(10)
                print("poll reply: {}".format(ret))
                if ret.find(b' \r\n') >= 0:
                    ret = ret[:-3]
                else:
                    ret = ret[:-2]
                poll_reply = [ int(X,16) for X in ret.decode('utf-8').split(" ")]
                if poll_reply[0] != addr:
                    if i < 3:
                        continue
                    else:
                        raise  MDBProtocol("got poll reply address != {0:02X}".format(addr))
                break


        return (ack , response, poll_reply)

        # end = time.time() + timeout
        # r = self.get_result(timeout)
        # print("got message: {}".format(r))
        # err, result = self._parse_result(r)
        # print('_parse_result return: {}, {}'.format(err, result))
        # self._print_cmd_sequence(cmd, err, result)
        # return err, result

    #[0x10]  REST    b'00 \r\n10 00\r\n'
    def reset(self, addr = CASHLESS_DEVICE1_ADDRESS):
        ack, res, p= self.do_cmd(addr, [0],adapter_response=True, mdb_response=False, poll = True)
        self._print_msg("RESET {0:02X} ACK:{1:b} POLL: {2:02X}".format(addr, ack, p[1]))
        return ack, res, p


    #[0x11, 0x00, 0x03, 0x00, 0x00, 0x00] CONFIG   b'01 01 00 9C 01 02 07 0D B5 \r\n'
    def setup_config(self, addr=CASHLESS_DEVICE1_ADDRESS, feature_level=2, display_column=0, display_row=0, display_info=0):
        self._print_msg("SET CONFIG DATA {0:02X}".format(addr))
        ack, res, p = self.do_cmd(addr, [ 1, 0x00,  feature_level,display_column,display_row,display_info ],adapter_response=False, mdb_response=True, poll=False)
        self.config = Config(res[1], res[2]*256+res[3], res[4], res[5], res[6], res[7])

        self._print_msg("SET CONFIG DATA {0:02X}, RETURN: {1:s}".format(addr, str(self.config)))
        return ack, res, p

    #[0x11, 0x01, 0xFF, 0xFF, 0, 0] SET PRICE    b'00 \r\n'
    def setup_price(self, addr=CASHLESS_DEVICE1_ADDRESS, max=0xFFFF, min=0):
        self._print_msg("SET PRICE {0:02X}".format(addr))
        ack, res, p = self.do_cmd(addr, [1, 0x01, (max&0xFF00)>>8, max&0xFF, (min&0xFF00)>>8, min&0xFF],adapter_response=True,mdb_response=False, poll=False)
        self._print_msg("SET PRICE {0:02X}, RETURN: {1:s}".format(addr, str(ack)))
        return ack, res, p

    #[0x14, 0x01] ENABLE    b'00 \r\n'
    def enable(self, addr=CASHLESS_DEVICE1_ADDRESS, enabled=True):
        self._print_msg("READER ENABLE {0:02X}".format(addr))
        ack, res, p = self.do_cmd(addr, [4, 0x01 if enabled else 0x00],adapter_response=True,mdb_response=False, poll=False)
        self._print_msg("READER ENABLE {0:02X}, RETURN(): {1:s}".format(addr, str(ack)))
        return ack, res, p

    #[0x17, ID(1byte), code(3byte),serial(5~16), model(17~28), software version(29~30)]
    def set_expansion_id(self, addr=CASHLESS_DEVICE1_ADDRESS, id=''):
        self._print_msg("Set expansion ID {0:02X}".format(addr))
        cmd = [7, 0x0]
        if not id:
            cmd = cmd + list(b'XXX') # your id
        cmd = cmd  + list(b'R2S00000000\0')
        cmd = cmd  + list(b'R2S00000000\0')
        cmd = cmd  + list(b'AA')

        ack, res, p = self.do_cmd(addr, cmd ,adapter_response=False,mdb_response=True, poll=False)
        self._print_msg("Set expansion {0:02X}, RETURN(): {1:s}".format(addr, str(ack)))
        return ack, res, p
#
    def init_device(self, addr=CASHLESS_DEVICE1_ADDRESS, enabled = False):
        ack, res, p = self.reset(addr=CASHLESS_DEVICE1_ADDRESS)
        ack, res, p = self.setup_config(addr=CASHLESS_DEVICE1_ADDRESS)
        ack, res, p = self.setup_price(addr=CASHLESS_DEVICE1_ADDRESS)
        self.set_expansion_id(addr=CASHLESS_DEVICE1_ADDRESS)
        ack, res, p = self.enable(addr=CASHLESS_DEVICE1_ADDRESS, enabled=enabled)
        if enabled:
            time.sleep(1)
            ack, res, p = self.enable(addr=CASHLESS_DEVICE1_ADDRESS, enabled=enabled)
        self.set_expansion_id(addr=CASHLESS_DEVICE1_ADDRESS)



    #b'10 03 07 D0 33 31 31 36 00 00 00\r\n'
    #level 1 : b'10 03 03 E8\r\n'
    #level 2 : b'10 03 07 D0 39 31 36 34 00 00 00\r\n'
    def begin_session(self, addr=CASHLESS_DEVICE1_ADDRESS,price=0,no=0xFFFF,timeout=10):
        self.set_expansion_id(addr=CASHLESS_DEVICE1_ADDRESS)
        if self.always_idle:
            ack, res, p = self.vend_request(price,no)
        else:
            r = self.get_poll_message(timeout=timeout)
            self._print_msg("poll message :{},{},{}".format(r, r[0], r[1]))
            if int(r[0]) != addr or int(r[1]) != 0x03:
                try:
                    self.end_session(addr)
                except:
                    pass
                raise MDBSequence("bad sequence or vend denied")
            pid = ""
            ptype = 0
            pdata = 0
            if len(r) > 4:
                pid = chr(r[4])+chr(r[5])+chr(r[6])+chr(r[7])
                ptype = r[8]

            session = SessionInfo(
                r[2]*256+r[3],
                pid,
                ptype,
                pdata
            )
            self._print_msg("begin session : {}".format(session))
            return session

    #[0x13, 0x0 , 0x0,  0x01, 0xFF, 0xFF]  VEND REQUEST (SELECT ITEM)   b'00 \r\n10 05 00 01\r\n'
    def vend_request(self, price, no=0xFFFF, addr=CASHLESS_DEVICE1_ADDRESS):
        if not self.config:
            raise MDBSequence('get config data before sending request')
        price0= price
        price = int(((price/100)/self.config._scale_factor)*(math.pow(10,self.config._decimal_places)))
        #price = int(((price) / self.config._scale_factor) * (math.pow(10, self.config._decimal_places)))
        #price = int(((price)/self.config._scale_factor)*(math.pow(10,self.config._decimal_places)))
        print("price {}, scale {}, places {} => {}".format(price0,self.config._scale_factor,self.config._decimal_places,price ))

        time.sleep(0.1)
        self.ser.flush()

        ack, res, p = self.do_cmd(addr, [3,0 , (price&0xFF00)>>256, price&0xFF, (no&0xFF00)>>256, no&0xFF ], adapter_response=True, mdb_response=False,
                                  poll=True, )
        self._print_msg("vend_request: {}, {}, {}".format(ack, res, p))
        if p[0] != addr or p[1] != 0x05:
            try:
                self.end_session()
            except:
                pass
            raise MDBSequence("out of sequence or denied")

        return ack, res, p

    #[0x13, 0x03]  FAILURE b'00 \r\n'
    def vend_failure(self, addr = CASHLESS_DEVICE1_ADDRESS):
        ack, res, p = self.do_cmd(addr, [3, 0x03 ], adapter_response=True, mdb_response=False,
                                  poll=False)
        self._print_msg("vend_failure: {}, {}, {}".format(ack, res, p))
        return ack, res, p

    # [0x13, 0x02]  SUCCESS
    def vend_success(self, addr = CASHLESS_DEVICE1_ADDRESS, no=0xFFFF):
        ack, res, p = self.do_cmd(addr, [3, 0x02, (0xFF00&no)>>8, no&0xFF], adapter_response=True, mdb_response=False,
                                  poll=False)
        self._print_msg("vend_success: {}, {}, {}".format(ack, res, p))
        return ack, res, p


    #[0x13, 0x04]  SESSION COMPLETE  b'00 \r\n10 07\r\n'
    def end_session(self, addr = CASHLESS_DEVICE1_ADDRESS):
        ack, res, p = self.do_cmd(addr, [3, 0x04], adapter_response=True, mdb_response=False,
                                  poll=True)
        self._print_msg("session complete: {}, {}, {}".format(ack, res, p))
        return ack, res, p

def test():
    ser = serial.Serial("/dev/ttyS1", baudrate=9600, timeout=10)
    m=MDBCashless(ser)
    # m.reset()
    # m.setup_config()
    # m.setup_price()
    # m.enable(enabled=False)

    #level 1
    # m.init_device(enabled = False)
    # time.sleep(2)
    #
    # m.enable(enabled=True)
    # m.begin_session(timeout=10)
    # print("*********** first request")
    # m.vend_request(1)
    # time.sleep(5)
    # m.vend_success()
    #
    # print("*********** second request")
    # #time.sleep(1)
    # m.vend_request(1)
    #
    # time.sleep(5)
    #
    # #m.vend_success()
    #
    # m.end_session()
    #
    # m.enable(enabled=False)

    #level 3 always idle
    m.init_device(enabled=True)
    m.always_idle=True
    m.begin_session(price=1, no=0xFFFF)
    time.sleep(5)
    m.vend_failure()

    m.end_session()




    # try:
    #
    # except:
    #     pass
    #
    #
    # m.enable(enabled=True)


if __name__ == '__main__':
    test()