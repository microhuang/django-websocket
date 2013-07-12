#encoding:utf-8
import os
import array
import logging
import struct
import select
import socket
from errno import EINTR


logger = logging.getLogger(__name__)


class WebSocketProtocol(object):

    LENGTH_7 = 0x7d
    LENGTH_16 = 1 << 16
    LENGTH_63 = 1 << 63
    OPCODE_TEXT = 0x1
    OPCODE_BINARY = 0x2
    OPCODE_CLOSE = 0x8
    OPCODE_PING = 0x9
    OPCODE_PONG = 0xa
    STATUS_NORMAL = 1000
    STATUS_GOING_AWAY = 1001
    STATUS_PROTOCOL_ERROR = 1002
    STATUS_UNSUPPORTED_DATA_TYPE = 1003
    STATUS_STATUS_NOT_AVAILABLE = 1005
    STATUS_ABNORMAL_CLOSED = 1006
    STATUS_INVALID_PAYLOAD = 1007
    STATUS_POLICY_VIOLATION = 1008
    STATUS_MESSAGE_TOO_BIG = 1009
    STATUS_INVALID_EXTENSION = 1010
    STATUS_UNEXPECTED_CONDITION = 1011
    STATUS_TLS_HANDSHAKE_ERROR = 1015

    def __init__(self, sock, handshake_reply=None, mask_outgoing=False):
        self.sock = sock
        self.closed = False
        self.handshake_reply = handshake_reply
        self.mask_outgoing = mask_outgoing

    def recv(self):
        """
        Receive string data(byte array) from the server.

        return value: string(byte array) value.
        """
        _, data = self.recv_data()
        return data

    def ping(self, payload=""):
        """
        send ping data.

        payload: data payload to send server.
        """
        self.send(payload, self.OPCODE_PING)

    def pong(self, payload):
        """
        send pong data.

        payload: data payload to send server.
        """
        self.send(payload, self.OPCODE_PONG)

    @classmethod
    def mask_or_unmask(cls, mask_key, data):
        """
        mask or unmask data. Just do xor for each byte

        mask_key: 4 byte string(byte).

        data: data to mask/unmask.
        """
        _m = array.array("B", mask_key)
        _d = array.array("B", data)
        for i in xrange(len(_d)):
            _d[i] ^= _m[i % 4]
        return _d.tostring()

    def recv_data(self):
        """
        Recieve data with operation code.

        return  value: tuple of operation code and string(byte array) value.
        """
        while True:
            fin, opcode, data = self.recv_frame()
            if not fin and not opcode and not data:
                # handle error:
                # 'NoneType' object has no attribute 'opcode'
                raise ValueError(
                    "Not a valid fin %s opcode %s data %s" % (fin, opcode, data))
            elif opcode in (
                self.OPCODE_TEXT,
                self.OPCODE_BINARY
            ):
                return (opcode, data)
            elif opcode == self.OPCODE_CLOSE:
                self.close()
                return (opcode, None)
            elif opcode == self.OPCODE_PING:
                self.pong(data)

    def recv_frame(self):
        """
        recieve data as frame from server.
        """
        header_bytes = self._recv_strict(2)
        if not header_bytes:
            return None, None, None
        b1 = ord(header_bytes[0])
        fin = b1 >> 7 & 1
        opcode = b1 & 0xf
        b2 = ord(header_bytes[1])
        mask = b2 >> 7 & 1
        length = b2 & 0x7f

        length_data = ""
        if length == 0x7e:
            length_data = self._recv_strict(2)
            length = struct.unpack("!H", length_data)[0]
        elif length == 0x7f:
            length_data = self._recv_strict(8)
            length = struct.unpack("!Q", length_data)[0]
        mask_key = ""
        if mask:
            mask_key = self._recv_strict(4)
        data = self._recv_strict(length)
        if mask:
            data = self.mask_or_unmask(mask_key, data)
        return fin, opcode, data

    def _recv_strict(self, bufsize):
        remaining = bufsize
        _bytes = ""
        while remaining:
            _buffer = self.sock.recv(bufsize)
            if not _buffer:
                raise socket.error('socket closed')
            _bytes += _buffer
            remaining = bufsize - len(_bytes)

        return _bytes

    def send_close(self, status=STATUS_NORMAL, reason=""):
        """
        send close data to the server.
        reason: the reason to close. This must be string.
        """
        if status < 0 or status >= self.LENGTH_16:
            raise ValueError("code is invalid range")
        self.send(struct.pack('!H', status) + reason, self.OPCODE_CLOSE)

    def send_handshake_replay(self):
        if self.handshake_reply:
            self.sock.sendall(self.handshake_reply)

    def can_recv(self, timeout=0.0):
        '''
        Return ``True`` if new data can be read from the socket.
        '''
        r, w, e = [self.sock], [], []
        try:
            r, w, e = select.select(r, w, e, timeout)
        except select.error as err:
            if err.args[0] == EINTR:
                return False
            raise
        return self.sock in r

    def _write_frame(self, fin, opcode, data):
        if fin:
            finbit = 0x80
        else:
            finbit = 0
        frame = struct.pack("B", finbit | opcode)
        l = len(data)
        if self.mask_outgoing:
            mask_bit = 0x80
        else:
            mask_bit = 0
        if l < 126:
            frame += struct.pack("B", l | mask_bit)
        elif l <= 0xFFFF:
            frame += struct.pack("!BH", 126 | mask_bit, l)
        else:
            frame += struct.pack("!BQ", 127 | mask_bit, l)
        if self.mask_outgoing:
            mask = os.urandom(4)
            data = mask + self._apply_mask(mask, data)
        frame += data
        self.sock.send(frame)

    def send(self, message, binary=False):
        """Sends the given message to the client of this Web Socket."""
        if binary:
            opcode = 0x2
        else:
            opcode = 0x1
        message = message.encode('utf8')
        try:
            self._write_frame(True, opcode, message)
        except IOError as e:
            logger.debug(e)
            self.close()

    def close(self):
        self.closed = True
        self.sock.close()
