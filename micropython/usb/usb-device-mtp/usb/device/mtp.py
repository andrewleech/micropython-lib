# MicroPython USB MTP module
# MIT license; Copyright (c) 2024 MicroPython Developers
from micropython import const, schedule
import struct
import time
import os
import io
import errno
import uctypes
import shutil

from .core import Interface, Buffer, split_bmRequestType

_EP_IN_FLAG = const(1 << 7)

# Control transfer stages
_STAGE_IDLE = const(0)
_STAGE_SETUP = const(1)
_STAGE_DATA = const(2)
_STAGE_ACK = const(3)

# Request types
_REQ_TYPE_STANDARD = const(0x0)
_REQ_TYPE_CLASS = const(0x1)
_REQ_TYPE_VENDOR = const(0x2)
_REQ_TYPE_RESERVED = const(0x3)

# USB Classes
_INTERFACE_CLASS_STILL_IMAGE = const(0x06)
_INTERFACE_SUBCLASS_STILL_IMAGE = const(0x01)
_INTERFACE_PROTOCOL_PIMA_15740 = const(0x01)

# MTP Container Types
_MTP_CONTAINER_TYPE_COMMAND = const(1)
_MTP_CONTAINER_TYPE_DATA = const(2)
_MTP_CONTAINER_TYPE_RESPONSE = const(3)
_MTP_CONTAINER_TYPE_EVENT = const(4)

# Standard MTP Operations
_MTP_OPERATION_GET_DEVICE_INFO = const(0x1001)
_MTP_OPERATION_OPEN_SESSION = const(0x1002)
_MTP_OPERATION_CLOSE_SESSION = const(0x1003)
_MTP_OPERATION_GET_STORAGE_IDS = const(0x1004)
_MTP_OPERATION_GET_STORAGE_INFO = const(0x1005)
_MTP_OPERATION_GET_NUM_OBJECTS = const(0x1006)
_MTP_OPERATION_GET_OBJECT_HANDLES = const(0x1007)
_MTP_OPERATION_GET_OBJECT_INFO = const(0x1008)
_MTP_OPERATION_GET_OBJECT = const(0x1009)
_MTP_OPERATION_GET_PARTIAL_OBJECT = const(0x101A)
_MTP_OPERATION_DELETE_OBJECT = const(0x100B)
_MTP_OPERATION_SEND_OBJECT_INFO = const(0x100C)
_MTP_OPERATION_SEND_OBJECT = const(0x100D)

# MTP Response Codes
_MTP_RESPONSE_OK = const(0x2001)
_MTP_RESPONSE_GENERAL_ERROR = const(0x2002)
_MTP_RESPONSE_SESSION_NOT_OPEN = const(0x2003)
_MTP_RESPONSE_INVALID_TRANSACTION_ID = const(0x2004)
_MTP_RESPONSE_OPERATION_NOT_SUPPORTED = const(0x2005)
_MTP_RESPONSE_PARAMETER_NOT_SUPPORTED = const(0x2006)
_MTP_RESPONSE_INCOMPLETE_TRANSFER = const(0x2007)
_MTP_RESPONSE_INVALID_STORAGE_ID = const(0x2008)
_MTP_RESPONSE_INVALID_OBJECT_HANDLE = const(0x2009)
_MTP_RESPONSE_STORE_FULL = const(0x200C)
_MTP_RESPONSE_STORE_READ_ONLY = const(0x200E)
_MTP_RESPONSE_PARTIAL_DELETION = const(0x2012)
_MTP_RESPONSE_STORE_NOT_AVAILABLE = const(0x2013)
_MTP_RESPONSE_SPECIFICATION_BY_FORMAT_UNSUPPORTED = const(0x2014)
_MTP_RESPONSE_INVALID_PARENT_OBJECT = const(0x201A)
_MTP_RESPONSE_INVALID_PARAMETER = const(0x201D)
_MTP_RESPONSE_SESSION_ALREADY_OPEN = const(0x201E)

# MTP Format Codes
_MTP_FORMAT_UNDEFINED = const(0x3000)
_MTP_FORMAT_ASSOCIATION = const(0x3001)  # directory
_MTP_FORMAT_TEXT = const(0x3004)
_MTP_FORMAT_HTML = const(0x3005)
_MTP_FORMAT_WAV = const(0x3008)
_MTP_FORMAT_MP3 = const(0x3009)
_MTP_FORMAT_AVI = const(0x300A)
_MTP_FORMAT_MPEG = const(0x300B)
_MTP_FORMAT_ASF = const(0x300C)
_MTP_FORMAT_EXIF_JPEG = const(0x3801)
_MTP_FORMAT_TIFF_EP = const(0x380D)
_MTP_FORMAT_BMP = const(0x3804)
_MTP_FORMAT_GIF = const(0x3807)
_MTP_FORMAT_JFIF = const(0x3808)
_MTP_FORMAT_PNG = const(0x380B)
_MTP_FORMAT_TIFF = const(0x380D)
_MTP_FORMAT_JP2 = const(0x380F)
_MTP_FORMAT_JPX = const(0x3810)

# MTP Object Property Codes
_MTP_OBJECT_PROP_STORAGE_ID = const(0xDC01)
_MTP_OBJECT_PROP_OBJECT_FORMAT = const(0xDC02)
_MTP_OBJECT_PROP_PROTECTION_STATUS = const(0xDC03)
_MTP_OBJECT_PROP_OBJECT_SIZE = const(0xDC04)
_MTP_OBJECT_PROP_OBJECT_FILE_NAME = const(0xDC07)
_MTP_OBJECT_PROP_DATE_CREATED = const(0xDC08)
_MTP_OBJECT_PROP_DATE_MODIFIED = const(0xDC09)
_MTP_OBJECT_PROP_PARENT_OBJECT = const(0xDC0B)
_MTP_OBJECT_PROP_PERSISTENT_UID = const(0xDC41)
_MTP_OBJECT_PROP_NAME = const(0xDC44)

# Storage Types
_MTP_STORAGE_FIXED_RAM = const(0x0001)
_MTP_STORAGE_REMOVABLE_RAM = const(0x0002)
_MTP_STORAGE_REMOVABLE_ROM = const(0x0003)
_MTP_STORAGE_FIXED_ROM = const(0x0004)
_MTP_STORAGE_REMOVABLE_MEDIA = const(0x0005)
_MTP_STORAGE_FIXED_MEDIA = const(0x0006)

# Filesystem Access Capability
_MTP_STORAGE_READ_WRITE = const(0x0000)
_MTP_STORAGE_READ_ONLY_WITHOUT_DELETE = const(0x0001)
_MTP_STORAGE_READ_ONLY_WITH_DELETE = const(0x0002)

# Maximum sizes and buffers
# _MAX_PACKET_SIZE = const(512)
_MAX_PACKET_SIZE = const(64)
_DEFAULT_TX_BUF_SIZE = const(1024)
_DEFAULT_RX_BUF_SIZE = const(1024)
_CONTAINER_HEADER_SIZE = const(12)

# Length of the bulk transfer endpoints. Maybe should be configurable?
_BULK_EP_LEN = const(64)

# MTP struct definitions using uctypes
# Container header struct
_MTP_CONTAINER_HEADER_DESC = {
    "length": 0 | uctypes.UINT32,
    "type": 4 | uctypes.UINT16,
    "code": 6 | uctypes.UINT16,
    "transaction_id": 8 | uctypes.UINT32
}

# Device Info struct
_MTP_DEVICE_INFO_DESC = {
    "standard_version": 0 | uctypes.UINT16,
    "vendor_extension_id": 2 | uctypes.UINT32,
    "mtp_version": 6 | uctypes.UINT16,
    # Variable length data follows: extension string, operations, events, etc.
}

# Storage IDs struct
_MTP_STORAGE_ID = const(0x00010001)

_MTP_STORAGE_IDS_DESC = {
    "count": 0 | uctypes.UINT32,
    "storage_ids": (4 | uctypes.ARRAY, 1 | uctypes.UINT32)  # Variable length array
}

# Storage Info struct
_MTP_STORAGE_INFO_DESC = {
    "storage_type": 0 | uctypes.UINT16,
    "filesystem_type": 2 | uctypes.UINT16,
    "access_capability": 4 | uctypes.UINT16,
    "max_capacity": 6 | uctypes.UINT64,
    "free_space": 14 | uctypes.UINT64,
    "free_space_objects": 22 | uctypes.UINT32
    # Variable length data follows: storage_description, volume_identifier
}

# Object Handles struct
_MTP_OBJECT_HANDLES_DESC = {
    "count": 0 | uctypes.UINT32,
    "handles": (4 | uctypes.ARRAY, 1 | uctypes.UINT32)  # Variable length array
}


class MTPInterface(Interface):
    """USB MTP device interface for MicroPython.
    
    This class implements a basic MTP (Media Transfer Protocol) interface
    that allows USB hosts to access the MicroPython filesystem.
    """
    
    def __init__(self, storage_path="/", rx_size=_DEFAULT_RX_BUF_SIZE, tx_size=_DEFAULT_TX_BUF_SIZE, debug=False):
        """Initialize the MTP interface.
        
        Args:
            storage_path: Root path to expose via MTP (default: "/")
            rx_size: Size of the receive buffer in bytes
            tx_size: Size of the transmit buffer in bytes
            debug: Enable debug logging (default: False)
        """
        super().__init__()
        
        # Debug flag
        self._debug = debug
        
        # USB endpoints set during enumeration
        self.ep_in = None  # Bulk IN endpoint (device to host)
        self.ep_out = None  # Bulk OUT endpoint (host to device)
        self.ep_intr = None  # Interrupt IN endpoint (for events)
        
        # Buffers for data transfer
        self._rx = Buffer(rx_size)
        self._tx = Buffer(tx_size)
        
        # MTP session state
        self._session_open = False
        self._session_id = 0
        self._transaction_id = 0
        
        # Filesystem attributes
        self._storage_path = storage_path
        self._storage_id = _MTP_STORAGE_ID  # Fixed ID for the single storage we support
        self._next_object_handle = 0x00000001  # Start object handles at 1
        self._object_handles = {}  # Maps object handles to file paths
        self._parent_map = {}  # Maps handles to parent handles
        
        # Pending operation state
        self._current_operation = None
        self._current_params = None
        self._data_expected = False
        self._data_phase_complete = False
        
        # Object transfer state
        self._send_object_handle = None
        self._send_object_info = None
        self._get_object_handle = None
        
    def _log(self, msg, *args):
        """Print a debug message if debug logging is enabled."""
        if self._debug:
            if args:
                print("[MTP] " + msg.format(*args))
            else:
                print("[MTP] " + msg)
        
    def desc_cfg(self, desc, itf_num, ep_num, strs):
        """Build the USB configuration descriptor for this interface."""
        self._log("Building descriptors: itf_num={}, ep_num={}", itf_num, ep_num)

        # Add the interface identifier to the strings
        i_interface = len(strs)
        strs.append("MTP")

        # Add the interface descriptor for MTP (PIMA 15740 Still Image)
        desc.interface(
            itf_num, 
            3,  # Number of endpoints (1 bulk IN, 1 bulk OUT, 1 interrupt IN)
            _INTERFACE_CLASS_STILL_IMAGE,
            _INTERFACE_SUBCLASS_STILL_IMAGE,
            _INTERFACE_PROTOCOL_PIMA_15740,
            i_interface,
        )
        
        # Add the endpoints (bulk OUT, bulk IN, interrupt IN)
        self.ep_out = ep_num
        self.ep_in = ep_num | _EP_IN_FLAG
        self.ep_intr = (ep_num + 1) | _EP_IN_FLAG
        
        self._log("Endpoints assigned: ep_out=0x{:02x}, ep_in=0x{:02x}, ep_intr=0x{:02x}",
            self.ep_out, self.ep_in, self.ep_intr)
        
        desc.endpoint(self.ep_out, "bulk", _MAX_PACKET_SIZE, 0)
        desc.endpoint(self.ep_in, "bulk", _MAX_PACKET_SIZE, 0)
        desc.endpoint(self.ep_intr, "interrupt", 8, 10)  # 10ms interval for events
    
    def num_eps(self):
        """Return the number of endpoints used by this interface."""
        return 3  # We use 2 endpoint numbers (3 endpoints total with IN flag)
    
    def on_open(self):
        """Called when the USB host configures the device."""
        self._log("Device configured by host")
        super().on_open()
        # Start transfers for receiving commands and data
        self._rx_xfer()
        self._log("Interface ready, waiting for MTP commands")
        
    def on_reset(self):
        """Called when the USB device is reset by the host."""
        self._log("Device reset by host")
        super().on_reset()
        # Reset the session state
        self._session_open = False
        self._session_id = 0
        self._transaction_id = 0
        self._current_operation = None
        self._current_params = None
        self._data_expected = False
        self._data_phase_complete = False
        self._send_object_handle = None
        self._send_object_info = None
        self._get_object_handle = None
        self._log("Session state cleared")
    
    def _rx_xfer(self):
        """Submit a new transfer to receive data from the host."""
        if self.is_open() and not self.xfer_pending(self.ep_out) and self._rx.writable():
            buf = self._rx.pend_write(_BULK_EP_LEN)
            self._log("Submitting OUT transfer, buffer size={}", len(buf))
            self.submit_xfer(self.ep_out, buf, self._rx_cb)
        else:
            if not self.is_open():
                self._log("Cannot submit OUT transfer - interface not open")
            elif self.xfer_pending(self.ep_out):
                self._log("Cannot submit OUT transfer - transfer already pending")
            elif not self._rx.writable():
                self._log("Cannot submit OUT transfer - RX buffer full ({} bytes)", self._rx.readable())
    
    def _rx_cb(self, ep, res, num_bytes):
        """Callback when data is received from the host."""
        # self._log("OUT transfer complete: res={}, bytes={}", res, num_bytes)
        if res == 0:
            self._rx.finish_write(num_bytes)
            # self._log("Scheduling data processing")
            schedule(self._process_rx, None)
        # else:
            # self._log("OUT transfer failed with error {}", res)
        self._rx_xfer()  # Continue receiving
    
    def _tx_xfer(self, quiet=False):
        """Submit a new transfer to send data to the host."""
        if self.is_open() and not self.xfer_pending(self.ep_in) and self._tx.readable():
            buf = self._tx.pend_read()#[0:_BULK_EP_LEN]
            self._log("Submitting IN transfer, data size={}", len(buf))
            self.submit_xfer(self.ep_in, buf, self._tx_cb)
        else:
            if quiet:
                return
            if not self.is_open():
                self._log("Cannot submit IN transfer - interface not open")
            elif self.xfer_pending(self.ep_in):
                self._log("Cannot submit IN transfer - transfer already pending")
            elif not self._tx.readable():
                self._log("Cannot submit IN transfer - no data in TX buffer")
    
    def _tx_cb(self, ep, res, num_bytes):
        """Callback when data has been sent to the host."""
        self._log("IN transfer complete: res={}, bytes={}", res, num_bytes)
        if res == 0:
            self._tx.finish_read(num_bytes)
        else:
            self._log("IN transfer failed with error {}", res)
        self._tx_xfer()  # Send more data if available
    
    def _process_rx(self, _):
        """Process received data from the host."""
        # Check if there's enough data for a container header
        readable = self._rx.readable()
        if readable < _CONTAINER_HEADER_SIZE:
            self._log("Not enough data for container header ({} bytes)", readable)
            return
        
        # Peek at the container header without consuming it yet
        header = self._rx.pend_read()
        
        # Parse container header using uctypes
        # Create a container header struct over the header buffer
        hdr = uctypes.struct(uctypes.addressof(header), _MTP_CONTAINER_HEADER_DESC, uctypes.LITTLE_ENDIAN)
        
        # Extract values from the struct
        length = hdr.length
        container_type = hdr.type
        code = hdr.code
        transaction_id = hdr.transaction_id
        
        container_types = {
            _MTP_CONTAINER_TYPE_COMMAND: "COMMAND",
            _MTP_CONTAINER_TYPE_DATA: "DATA",
            _MTP_CONTAINER_TYPE_RESPONSE: "RESPONSE",
            _MTP_CONTAINER_TYPE_EVENT: "EVENT"
        }
        
        container_type_str = container_types.get(container_type, "UNKNOWN")
        self._log("Container header: length={}, type={}, code=0x{:04x}, transaction_id={}",
            length, container_type_str, code, transaction_id)
        
        # Ensure we have the complete container
        if self._rx.readable() < length:
            self._log("Waiting for complete container ({}/{} bytes)", self._rx.readable(), length)
            return
        
        # Now consume the container header
        self._rx.finish_read(_CONTAINER_HEADER_SIZE)
        
        # Process based on container type
        if container_type == _MTP_CONTAINER_TYPE_COMMAND:
            # Extract parameters (up to 5)
            param_count = (length - _CONTAINER_HEADER_SIZE) // 4
            params = []
            for i in range(min(param_count, 5)):
                if self._rx.readable() >= 4:
                    param_data = self._rx.pend_read()
                    param = struct.unpack_from("<I", param_data, 0)[0]
                    params.append(param)
                    self._rx.finish_read(4)
            
            # Map code to operation name for common operations
            operation_names = {
                _MTP_OPERATION_GET_DEVICE_INFO: "GetDeviceInfo",
                _MTP_OPERATION_OPEN_SESSION: "OpenSession",
                _MTP_OPERATION_CLOSE_SESSION: "CloseSession",
                _MTP_OPERATION_GET_STORAGE_IDS: "GetStorageIDs",
                _MTP_OPERATION_GET_STORAGE_INFO: "GetStorageInfo",
                _MTP_OPERATION_GET_NUM_OBJECTS: "GetNumObjects",
                _MTP_OPERATION_GET_OBJECT_HANDLES: "GetObjectHandles",
                _MTP_OPERATION_GET_OBJECT_INFO: "GetObjectInfo",
                _MTP_OPERATION_GET_OBJECT: "GetObject",
                _MTP_OPERATION_DELETE_OBJECT: "DeleteObject",
                _MTP_OPERATION_SEND_OBJECT_INFO: "SendObjectInfo",
                _MTP_OPERATION_SEND_OBJECT: "SendObject"
            }
            
            op_name = operation_names.get(code, "Unknown")
            self._log("Received command: {} (0x{:04x}), params={}", op_name, code, params)
            
            # Store operation info for processing
            self._current_operation = code
            self._current_params = params
            self._transaction_id = transaction_id
            self._data_expected = False
            self._data_phase_complete = False
            
            # Handle the command
            self._handle_command()
        
        elif container_type == _MTP_CONTAINER_TYPE_DATA:
            if not self._current_operation or self._data_phase_complete:
                # Unexpected data phase
                self._log("Unexpected data phase, no operation in progress")
                self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
                return
            
            # Process the data phase
            data_size = length - _CONTAINER_HEADER_SIZE
            self._log("Data phase: size={} bytes for operation 0x{:04x}",
                data_size, self._current_operation)
            
            if self._rx.readable() >= data_size:
                data = bytearray(data_size)
                view = memoryview(data)
                remaining = data_size
                position = 0
                
                while remaining > 0:
                    buf = self._rx.pend_read()
                    chunk = min(len(buf), remaining)
                    view[position:position+chunk] = buf[:chunk]
                    self._rx.finish_read(chunk)
                    position += chunk
                    remaining -= chunk
                
                self._log("Data phase complete, processing {} bytes", data_size)
                self._process_data_phase(data)
            else:
                # Not enough data received
                # Skip incomplete data
                self._log("Incomplete data received, skipping ({}/{} bytes)",
                    self._rx.readable(), data_size)
                self._rx.finish_read(self._rx.readable())
                self._send_response(_MTP_RESPONSE_INCOMPLETE_TRANSFER)
    
    def _handle_command(self):
        """Process an MTP command based on the current operation code."""
        op = self._current_operation
        params = self._current_params
        
        # Check if session is open (required for most operations)
        if not self._session_open and op != _MTP_OPERATION_OPEN_SESSION and op != _MTP_OPERATION_GET_DEVICE_INFO:
            self._log("Rejecting command 0x{:04x} - session not open", op)
            self._send_response(_MTP_RESPONSE_SESSION_NOT_OPEN)
            return
        
        # Map the operation code to a name for better debug messages
        operation_names = {
            _MTP_OPERATION_GET_DEVICE_INFO: "GetDeviceInfo",
            _MTP_OPERATION_OPEN_SESSION: "OpenSession",
            _MTP_OPERATION_CLOSE_SESSION: "CloseSession",
            _MTP_OPERATION_GET_STORAGE_IDS: "GetStorageIDs",
            _MTP_OPERATION_GET_STORAGE_INFO: "GetStorageInfo",
            _MTP_OPERATION_GET_NUM_OBJECTS: "GetNumObjects",
            _MTP_OPERATION_GET_OBJECT_HANDLES: "GetObjectHandles",
            _MTP_OPERATION_GET_OBJECT_INFO: "GetObjectInfo",
            _MTP_OPERATION_GET_OBJECT: "GetObject",
            _MTP_OPERATION_DELETE_OBJECT: "DeleteObject",
            _MTP_OPERATION_SEND_OBJECT_INFO: "SendObjectInfo",
            _MTP_OPERATION_SEND_OBJECT: "SendObject"
        }
        op_name = operation_names.get(op, "Unknown")
        self._log("Processing command: {} (0x{:04x})", op_name, op)
        
        # Handle operations
        if op == _MTP_OPERATION_GET_DEVICE_INFO:
            self._cmd_get_device_info()
        elif op == _MTP_OPERATION_OPEN_SESSION:
            self._cmd_open_session(params)
        elif op == _MTP_OPERATION_CLOSE_SESSION:
            self._cmd_close_session()
        elif op == _MTP_OPERATION_GET_STORAGE_IDS:
            self._cmd_get_storage_ids()
        elif op == _MTP_OPERATION_GET_STORAGE_INFO:
            self._cmd_get_storage_info(params)
        elif op == _MTP_OPERATION_GET_NUM_OBJECTS:
            self._cmd_get_num_objects(params)
        elif op == _MTP_OPERATION_GET_OBJECT_HANDLES:
            self._cmd_get_object_handles(params)
        elif op == _MTP_OPERATION_GET_OBJECT_INFO:
            self._cmd_get_object_info(params)
        elif op == _MTP_OPERATION_GET_OBJECT:
            self._cmd_get_object(params)
        elif op == _MTP_OPERATION_DELETE_OBJECT:
            self._cmd_delete_object(params)
        elif op == _MTP_OPERATION_SEND_OBJECT_INFO:
            self._cmd_send_object_info(params)
        elif op == _MTP_OPERATION_SEND_OBJECT:
            self._cmd_send_object()
        else:
            # Operation not supported
            self._log("Unsupported operation: 0x{:04x}", op)
            self._send_response(_MTP_RESPONSE_OPERATION_NOT_SUPPORTED)
    
    def _process_data_phase(self, data):
        """Process data received during a data phase of an MTP transaction."""
        op = self._current_operation
        self._data_phase_complete = True
        
        if op == _MTP_OPERATION_SEND_OBJECT_INFO:
            self._process_send_object_info(data)
        elif op == _MTP_OPERATION_SEND_OBJECT:
            self._process_send_object(data)
        else:
            # Unexpected data for this operation
            self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
    
    def _cmd_get_device_info(self):
        """Handle GetDeviceInfo command."""
        self._log("Generating device info response")
        
        # Allocate a buffer for device info
        data = bytearray(512)  # Pre-allocate buffer - device info has variable length
        
        # Create a device info struct
        dev_info = uctypes.struct(uctypes.addressof(data), _MTP_DEVICE_INFO_DESC, uctypes.LITTLE_ENDIAN)
        
        # Fill in the fixed fields
        dev_info.standard_version = 100  # Version 1.00
        dev_info.vendor_extension_id = 0x00000006  # Microsoft MTP Extension
        dev_info.mtp_version = 100  # Version 1.00
        
        # Handle variable-length data after the fixed struct
        offset = 8  # Start after the fixed part of the struct
        

        # MTP extensions description string - Microsoft extension
        # MTP extension strings are ASCII strings in PIMA format (8-bit length + 8-bit chars with null terminator)
        offset += self._write_mtp_string(data, offset, "microsoft.com: 1.0")
        # ext_string = "microsoft.com: 1.0"  # Standard Microsoft extension string
        
        # # String length (8-bit, including null terminator)
        # data[offset] = len(ext_string) * 2 + 1
        # offset += 1
        
        # # String data as ASCII
        # for c in ext_string:
        #     data[offset] = ord(c)
        #     offset += 1
        
        # # ASCII null terminator
        # data[offset] = 0
        # offset += 1
        
        # Functional mode
        struct.pack_into("<H", data, offset, 0)  # Standard mode
        offset += 2
        
        # Supported operations (array of operation codes)
        operations = [
            _MTP_OPERATION_GET_DEVICE_INFO,
            _MTP_OPERATION_OPEN_SESSION,
            _MTP_OPERATION_CLOSE_SESSION,
            _MTP_OPERATION_GET_STORAGE_IDS,
            _MTP_OPERATION_GET_STORAGE_INFO,
            _MTP_OPERATION_GET_NUM_OBJECTS,
            _MTP_OPERATION_GET_OBJECT_HANDLES,
            _MTP_OPERATION_GET_OBJECT_INFO,
            _MTP_OPERATION_GET_OBJECT,
            _MTP_OPERATION_DELETE_OBJECT,
            _MTP_OPERATION_SEND_OBJECT_INFO,
            _MTP_OPERATION_SEND_OBJECT,
        ]
        
        # Number of operations
        struct.pack_into("<I", data, offset, len(operations))
        offset += 4
        
        # List of operation codes
        for op in operations:
            struct.pack_into("<H", data, offset, op)
            offset += 2
            
        # Supported events (array of event codes) - empty for now
        struct.pack_into("<I", data, offset, 0)  # No events supported
        offset += 4
        
        # Supported device properties - empty for now
        struct.pack_into("<I", data, offset, 0)  # No device properties
        offset += 4
        
        # Supported capture formats - empty for now
        struct.pack_into("<I", data, offset, 0)  # No capture formats
        offset += 4
        
        # Supported playback formats (file formats we support)
        formats = [
            _MTP_FORMAT_ASSOCIATION,  # directories
            _MTP_FORMAT_TEXT,         # text files
            _MTP_FORMAT_UNDEFINED     # all other files
        ]
        
        # Number of formats
        struct.pack_into("<I", data, offset, len(formats))
        offset += 4
        
        # List of format codes
        for fmt in formats:
            struct.pack_into("<H", data, offset, fmt)
            offset += 2
        
        # MTP strings for device information (UTF-16 format)
        # Manufacturer
        offset += self._write_mtp_string(data, offset, "MicroPython")
        
        # Model
        offset += self._write_mtp_string(data, offset, "MicroPython MTP Device")
        
        # Device version
        offset += self._write_mtp_string(data, offset, "1.0")
        
        # Serial number
        offset += self._write_mtp_string(data, offset, "MP12345")  # Generic serial number
        
        # Send the device info
        self._send_data(data[:offset])
        
        # Then send success response
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_open_session(self, params):
        """Handle OpenSession command."""
        if not params:
            self._log("OpenSession: No parameters provided")
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
            return
            
        session_id = params[0]
        self._log("OpenSession: Requested session_id={}", session_id)
        
        if session_id == 0:
            self._log("OpenSession: Rejecting invalid session ID (0)")
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
        elif self._session_open:
            self._log("OpenSession: Session already open (id={})", self._session_id)
            self._send_response(_MTP_RESPONSE_SESSION_ALREADY_OPEN)
        else:
            self._log("OpenSession: Opening new session with id={}", session_id)
            self._session_open = True
            self._session_id = session_id
            
            # Refresh the object list when opening a session
            self._log("OpenSession: Refreshing object list")
            self._refresh_object_list()
            self._log("OpenSession: Found {} objects", len(self._object_handles))
            
            self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_close_session(self):
        """Handle CloseSession command."""
        self._log("CloseSession: Closing session {}", self._session_id)
        self._session_open = False
        self._session_id = 0
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_get_storage_ids(self):
        """Handle GetStorageIDs command."""
        # We only support a single storage
        self._log("GetStorageIDs: Reporting storage ID: 0x{:08x}", self._storage_id)
        
        # Create a buffer for storage IDs - 4 bytes for count, 4 bytes per storage ID
        data = bytearray(8)  # 4 bytes for count + 4 bytes for one storage ID
        
        # Create a storage IDs struct
        storage_ids = uctypes.struct(uctypes.addressof(data), _MTP_STORAGE_IDS_DESC, uctypes.LITTLE_ENDIAN)
        
        # Fill the struct
        storage_ids.count = 1  # We only support one storage
        storage_ids.storage_ids[0] = self._storage_id
        
        # Send the storage IDs array
        self._send_data(data)
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_get_storage_info(self, params):
        """Handle GetStorageInfo command."""
        self._log("Generating storage info for storage ID: 0x{:08x}", params[0] if params else 0)
        
        if not params or params[0] != self._storage_id:
            self._log("Invalid storage ID requested: 0x{:08x}", params[0] if params else 0)
            self._send_response(_MTP_RESPONSE_INVALID_STORAGE_ID)
            return
            
        # Get storage capacity information
        try:
            fs_stat = os.statvfs(self._storage_path)
            free_bytes = fs_stat[0] * fs_stat[4]  # f_bsize * f_bavail
            total_bytes = fs_stat[0] * fs_stat[2]  # f_bsize * f_blocks
            self._log("Storage stats: total={} bytes, free={} bytes", total_bytes, free_bytes)
        except Exception as e:
            # If we can't get stats, just return reasonable defaults
            self._log("Error getting storage stats: {}", str(e))
            free_bytes = 1024 * 1024  # 1MB
            total_bytes = 4 * 1024 * 1024  # 4MB
        
        # Create a buffer for storage info (fixed part is 26 bytes, plus variable-length strings)
        data = bytearray(128)
        
        # Create a storage info struct
        storage_info = uctypes.struct(uctypes.addressof(data), _MTP_STORAGE_INFO_DESC, uctypes.LITTLE_ENDIAN)
        
        # Fill in the fixed fields
        storage_info.storage_type = _MTP_STORAGE_FIXED_MEDIA
        storage_info.filesystem_type = 0x0002  # Generic hierarchical
        storage_info.access_capability = _MTP_STORAGE_READ_WRITE  # Read-write access
        storage_info.max_capacity = total_bytes
        storage_info.free_space = free_bytes
        storage_info.free_space_objects = 0xFFFFFFFF  # Maximum value - unknown
        
        # Handle variable-length data after the fixed struct
        offset = 26  # Start after the fixed part
        
        # Storage description
        offset += self._write_mtp_string(data, offset, "MicroPython Flash Storage")
        
        # Volume identifier (root)
        offset += self._write_mtp_string(data, offset, "MicroPython Storage")
        
        self._send_data(data[:offset])
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_get_num_objects(self, params):
        """Handle GetNumObjects command."""
        if not params:
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
            return
            
        storage_id = params[0]
        format_code = params[1] if len(params) > 1 else 0
        parent_handle = params[2] if len(params) > 2 else 0
        
        if storage_id != 0xFFFFFFFF and storage_id != self._storage_id:
            self._send_response(_MTP_RESPONSE_INVALID_STORAGE_ID)
            return
            
        # Count objects in the given parent
        count = 0
        for handle, parent in self._parent_map.items():
            if (parent_handle == 0 or parent == parent_handle) and handle in self._object_handles:
                # Apply format filter if specified
                if format_code == 0 or self._get_format_by_path(self._object_handles[handle]) == format_code:
                    count += 1
        
        # Send response with the count
        self._send_response(_MTP_RESPONSE_OK, [count])
    
    def _cmd_get_object_handles(self, params):
        """Handle GetObjectHandles command."""
        if not params:
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
            return
            
        storage_id = params[0]
        format_code = params[1] if len(params) > 1 else 0
        parent_handle = params[2] if len(params) > 2 else 0
        
        if storage_id != 0xFFFFFFFF and storage_id != self._storage_id:
            self._log("Error invalid storage id: {}", storage_id)
            self._send_response(_MTP_RESPONSE_INVALID_STORAGE_ID)
            return
            
        # Collect filtered handles
        handles = []
        self._log("get handles: {} {} {}", storage_id, format_code, parent_handle)
        self._log("handles: {}", self._object_handles)
        self._log("parent: {}", self._parent_map)

        for handle, parent in self._parent_map.items():
            if (parent_handle in (0, 0xFFFFFFFF) or parent == parent_handle) and handle in self._object_handles:
                # Apply format filter if specified
                if format_code == 0 or self._get_format_by_path(self._object_handles[handle]) == format_code:
                    handles.append(handle)
        
        # Create a buffer for the handles array
        data_size = 4 + len(handles) * 4  # 4 bytes for count + 4 bytes per handle
        data = bytearray(data_size)
        
        # For the _MTP_OBJECT_HANDLES_DESC, we need to dynamically adjust the array size
        # Create a custom descriptor with the actual array size
        obj_handles_desc = {
            "count": 0 | uctypes.UINT32,
            "handles": (4 | uctypes.ARRAY, len(handles) | uctypes.UINT32)
        }
        
        # Create the struct
        obj_handles = uctypes.struct(uctypes.addressof(data), obj_handles_desc, uctypes.LITTLE_ENDIAN)
        
        # Fill in the data
        obj_handles.count = len(handles)
        for i, handle in enumerate(handles):
            obj_handles.handles[i] = handle
        
        self._send_data(data)
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_get_object_info(self, params):
        """Handle GetObjectInfo command."""
        if not params:
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
            return
            
        object_handle = params[0]
        
        if object_handle not in self._object_handles:
            self._send_response(_MTP_RESPONSE_INVALID_OBJECT_HANDLE)
            return
            
        # Get the file information
        filepath = self._object_handles[object_handle]
        parent_handle = self._parent_map.get(object_handle, 0)
        
        try:
            stat = os.stat(filepath)
            filesize = stat[6]  # st_size
            is_dir = stat[0] & 0x4000  # S_IFDIR
            ctime = stat[9]  # st_ctime
            mtime = stat[8]  # st_mtime
        except:
            self._send_response(_MTP_RESPONSE_INVALID_OBJECT_HANDLE)
            return
        
        # Determine format code based on file type
        format_code = _MTP_FORMAT_ASSOCIATION if is_dir else self._get_format_by_path(filepath)
        
        # Get filename (basename of the path)
        parts = filepath.split('/')
        filename = parts[-1] if parts[-1] else parts[-2]  # Handle trailing slash for dirs
        self._log('***** filename {}', filename)
        # Prepare object info dataset
        data = bytearray(256)
        offset = 0
        
        # Storage ID
        struct.pack_into("<I", data, offset, self._storage_id)
        offset += 4
        
        # Object format
        struct.pack_into("<H", data, offset, format_code)
        offset += 2
        
        # Protection status (0 = no protection)
        struct.pack_into("<H", data, offset, 0)
        offset += 2
        
        # # Object size (in bytes)
        # struct.pack_into("<I", data, offset, filesize)
        # offset += 4
        
        # Object compressed size (same as size)
        struct.pack_into("<I", data, offset, filesize)
        offset += 4
        
        # Thumb format (0 = no thumbnail)
        struct.pack_into("<H", data, offset, 0)
        offset += 2
        
        # Thumb compressed size (0 = no thumbnail)
        struct.pack_into("<I", data, offset, 0)
        offset += 4
        
        # Thumb width/height (0 = no thumbnail)
        struct.pack_into("<II", data, offset, 0, 0)
        offset += 8
        
        # Image width/height (0 = not applicable)
        struct.pack_into("<II", data, offset, 0, 0)
        offset += 8
        
        # Image bit depth (0 = not applicable)
        struct.pack_into("<I", data, offset, 0)
        offset += 4
        
        # Parent object
        struct.pack_into("<I", data, offset, parent_handle)
        offset += 4
        
        # Association type (0 = undefined)
        struct.pack_into("<H", data, offset, 1 if is_dir else 0)  # 1 = Generic folder
        offset += 2
        
        # Association description (0 = not applicable)
        struct.pack_into("<I", data, offset, 0)
        offset += 4
        
        # Sequence number (0 = not applicable)
        struct.pack_into("<I", data, offset, 0)
        offset += 4
        
        # Filename
        offset += self._write_mtp_string(data, offset, filename)
        
        # Date created (as string) - format: YYYYMMDDThhmmss
        offset += self._write_mtp_string(data, offset, self._format_timestamp(ctime))
        
        # Date modified (as string)
        offset += self._write_mtp_string(data, offset, self._format_timestamp(mtime))
        
        # Keywords (empty string)
        offset += self._write_mtp_string(data, offset, "")
        
        self._send_data(data[:offset])
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_get_object(self, params):
        """Handle GetObject command."""
        if not params:
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
            return
            
        object_handle = params[0]
        
        if object_handle not in self._object_handles:
            self._send_response(_MTP_RESPONSE_INVALID_OBJECT_HANDLE)
            return
            
        filepath = self._object_handles[object_handle]
        
        try:
            # Check if it's a directory (we can't send directory contents)
            stat = os.stat(filepath)
            if stat[0] & 0x4000:  # S_IFDIR
                self._send_response(_MTP_RESPONSE_INVALID_OBJECT_HANDLE)
                return
                
            filesize = stat[6]  # st_size
            self._log("Sending file '{}', size: {} bytes", filepath, filesize)
            
            # Open the file and prepare to send it
            with open(filepath, "rb") as f:
                # Send container header with total size
                # We send this as a separate packet for better progress reporting on host
                container = bytearray(_CONTAINER_HEADER_SIZE)
                total_len = _CONTAINER_HEADER_SIZE + filesize
                
                struct.pack_into("<IHHI", container, 0, 
                                total_len,                    # Container length
                                _MTP_CONTAINER_TYPE_DATA,     # Container type
                                self._current_operation,      # Operation code
                                self._transaction_id)         # Transaction ID
                
                self._log("Sending DATA container header: length={}, operation=0x{:04x}, transaction_id={}",
                    total_len, self._current_operation, self._transaction_id)
                
                # Write header to TX buffer
                self._tx.write(container)
                print(f'readable: {self._tx.readable()}')
                # if self.is_open() and self.xfer_pending(self.ep_in):
                #     time.sleep_ms(11)

                # self._tx_xfer()
                # print(f'container passed')
                # if self.is_open() and self.xfer_pending(self.ep_in):
                #     time.sleep_ms(12)
                
                
                # Now send the file data in chunks
                bytes_sent = 0
                chunk_size = _MAX_PACKET_SIZE# min(_DEFAULT_TX_BUF_SIZE, len(self._tx._b))
                
                while bytes_sent < filesize:
                    # Wait until we can write to the TX buffer
                    while not self._tx.writable(): # or self._tx.writable() < chunk_size:
                        if not self.is_open():
                            self._log("Interface closed during file transfer")
                            return
                        
                        time.sleep_ms(10)
                        # self._tx_xfer(quiet=True)
                    
                    # Read a chunk from the file
                    remaining = filesize - bytes_sent
                    current_chunk_size = min(chunk_size, remaining, self._tx.writable())
                    chunk = f.read(current_chunk_size)
                    
                    if not chunk:
                        self._log("Unexpected end of file after {} bytes", bytes_sent)
                        break
                    
                    # Write chunk to TX buffer
                    self._tx.write(chunk)
                    bytes_sent += len(chunk)
                    
                    # Trigger transfer # if buffer is getting full
                    # if self._tx.writable() < chunk_size:
                    # if self._tx.readable() >= chunk_size:
                    self._tx_xfer(quiet=True)
                    
                    # Progress indicator for large files
                    if filesize > 10_000 and bytes_sent % 10_000 < chunk_size:
                        self._log("File transfer progress: {:.1f}%", (bytes_sent * 100.0) / filesize)
                
                while self._tx.readable():
                    self._tx_xfer()
                    time.sleep_ms(50)
                    
                # # Ensure final data is sent
                # self._tx_xfer()
                
                self._log("File transfer complete: {} bytes sent", bytes_sent)
                
                # Send the response
                self._send_response(_MTP_RESPONSE_OK)
                
        except OSError as e:
            self._log("Error sending file: {}", str(e))
            self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
    
    def _cmd_delete_object(self, params):
        """Handle DeleteObject command."""
        if not params:
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
            return
            
        object_handle = params[0]
        
        if object_handle not in self._object_handles:
            self._send_response(_MTP_RESPONSE_INVALID_OBJECT_HANDLE)
            return
            
        filepath = self._object_handles[object_handle]
        
        try:
            # Check if it's a directory
            stat = os.stat(filepath)
            is_dir = stat[0] & 0x4000  # S_IFDIR
            
            if is_dir:
                shutil.rmtree(filepath)
            else:
                # Delete the file
                os.remove(filepath)
                
            # Remove from our mappings
            del self._object_handles[object_handle]
            del self._parent_map[object_handle]
            
            # Send success response
            self._send_response(_MTP_RESPONSE_OK)
            
        except OSError:
            self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
    
    def _cmd_send_object_info(self, params):
        """Handle SendObjectInfo command (first phase)."""
        if not params or len(params) < 2:
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
            return
            
        storage_id = params[0]
        parent_handle = params[1]
        
        if storage_id != self._storage_id:
            self._send_response(_MTP_RESPONSE_INVALID_STORAGE_ID)
            return
            
        if parent_handle != 0 and parent_handle not in self._object_handles:
            self._send_response(_MTP_RESPONSE_INVALID_PARENT_OBJECT)
            return
            
        # We expect data from the host with object info
        self._data_expected = True
    
    def _process_send_object_info(self, data):
        """Process data received for SendObjectInfo command."""
        # Parse the object info dataset
        if len(data) < 32:  # Minimum required size
            self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
            return
            
        # Extract key fields
        storage_id = struct.unpack_from("<I", data, 0)[0]
        obj_format = struct.unpack_from("<H", data, 4)[0]
        parent_handle = struct.unpack_from("<I", data, 28)[0]
        
        # Find the filename from the dataset
        # First, find the offset where the filename is stored
        # This is a variable length structure, so we need to skip over elements
        offset = 52  # Start after fixed fields
        
        # Skip association description if present
        seq_num_offset = offset + 4
        
        # Now get the filename
        filename_len = struct.unpack_from("<H", data, seq_num_offset + 4)[0]
        filename_offset = seq_num_offset + 6
        
        # Extract the filename (stored as UTF-16)
        filename = ""
        for i in range(filename_len - 1):  # -1 to skip null terminator
            c = struct.unpack_from("<H", data, filename_offset + i*2)[0]
            if c == 0:  # Null terminator
                break
            filename += chr(c)
        
        # Determine parent path
        if parent_handle == 0:
            parent_path = self._storage_path
        else:
            parent_path = self._object_handles.get(parent_handle, self._storage_path)
            
        # Ensure parent path ends with a slash
        if not parent_path.endswith('/'):
            parent_path += '/'
        
        # Full path for the new object
        target_path = parent_path + filename
        
        # Generate a new object handle
        new_handle = self._next_object_handle
        self._next_object_handle += 1
        
        # Store the info for the next phase
        self._send_object_info = {
            'handle': new_handle,
            'path': target_path,
            'format': obj_format,
            'storage_id': storage_id,
            'parent': parent_handle
        }
        
        # Send response with the new object handle
        self._send_response(_MTP_RESPONSE_OK, [storage_id, parent_handle, new_handle])
    
    def _cmd_send_object(self):
        """Handle SendObject command (first phase)."""
        if not self._send_object_info:
            self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
            return
            
        # We expect data from the host with the object content
        self._data_expected = True
    
    def _process_send_object(self, data):
        """Process data received for SendObject command."""
        if not self._send_object_info:
            self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
            return
            
        target_path = self._send_object_info['path']
        new_handle = self._send_object_info['handle']
        obj_format = self._send_object_info['format']
        parent_handle = self._send_object_info['parent']
        
        try:
            # Create directory or file based on format
            if obj_format == _MTP_FORMAT_ASSOCIATION:
                try:
                    os.mkdir(target_path)
                except OSError as e:
                    if e.errno != errno.EEXIST:  # Ignore if directory already exists
                        raise
            else:
                # Write the file data
                with open(target_path, "wb") as f:
                    f.write(data)
                    
            # Update our object mappings
            self._object_handles[new_handle] = target_path
            self._parent_map[new_handle] = parent_handle
            
            # Send success response
            self._send_response(_MTP_RESPONSE_OK)
            
        except OSError:
            self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
        finally:
            # Clear the pending object info
            self._send_object_info = None
    
    def _send_data(self, data, final=True):
        """Send data phase of an MTP transaction."""
        if not self.is_open():
            self._log("Cannot send data - interface not open")
            return False
            
        # Create container header
        container = bytearray(_CONTAINER_HEADER_SIZE)
        total_len = _CONTAINER_HEADER_SIZE + len(data)
        
        # Create a container header struct
        header = uctypes.struct(uctypes.addressof(container), _MTP_CONTAINER_HEADER_DESC, uctypes.LITTLE_ENDIAN)
        
        # Fill in the container header fields
        header.length = total_len
        header.type = _MTP_CONTAINER_TYPE_DATA
        header.code = self._current_operation
        header.transaction_id = self._transaction_id
        
        self._log("Sending DATA container: length={}, operation=0x{:04x}, transaction_id={}{}",
            total_len, self._current_operation, self._transaction_id, 
            ", final=True" if final else "")
        
        # Send header
        self._tx.write(container)
        
        # Send data
        if data:
            if len(data) > 64 and self._debug:
                # Only log the data details if debug is enabled
                self._log("Data payload: {} bytes (first 64 bytes: {})",
                    len(data), [hex(b) for b in data[:64]])
            elif self._debug:
                self._log("Data payload: {} bytes: {}",
                    len(data), [hex(b) for b in data])
            else:
                self._log("Data payload: {} bytes", len(data))
                
            self._tx.write(data)
            
        # Start transfer
        self._tx_xfer()
        
        return True
    
    def _send_response(self, response_code, params=None):
        """Send response phase of an MTP transaction."""
        if not self.is_open():
            self._log("Cannot send response - interface not open")
            return False
        
        # Map response code to string for better debug messages
        response_names = {
            _MTP_RESPONSE_OK: "OK",
            _MTP_RESPONSE_GENERAL_ERROR: "GeneralError",
            _MTP_RESPONSE_SESSION_NOT_OPEN: "SessionNotOpen",
            _MTP_RESPONSE_INVALID_TRANSACTION_ID: "InvalidTransactionID",
            _MTP_RESPONSE_OPERATION_NOT_SUPPORTED: "OperationNotSupported",
            _MTP_RESPONSE_PARAMETER_NOT_SUPPORTED: "ParameterNotSupported",
            _MTP_RESPONSE_INCOMPLETE_TRANSFER: "IncompleteTransfer",
            _MTP_RESPONSE_INVALID_STORAGE_ID: "InvalidStorageID",
            _MTP_RESPONSE_INVALID_OBJECT_HANDLE: "InvalidObjectHandle",
            _MTP_RESPONSE_STORE_FULL: "StoreFull",
            _MTP_RESPONSE_STORE_READ_ONLY: "StoreReadOnly",
            _MTP_RESPONSE_PARTIAL_DELETION: "PartialDeletion",
            _MTP_RESPONSE_STORE_NOT_AVAILABLE: "StoreNotAvailable",
            _MTP_RESPONSE_SPECIFICATION_BY_FORMAT_UNSUPPORTED: "SpecificationByFormatUnsupported",
            _MTP_RESPONSE_INVALID_PARENT_OBJECT: "InvalidParentObject",
            _MTP_RESPONSE_INVALID_PARAMETER: "InvalidParameter",
            _MTP_RESPONSE_SESSION_ALREADY_OPEN: "SessionAlreadyOpen"
        }
        response_name = response_names.get(response_code, "Unknown")
            
        # Calculate response length
        param_count = len(params) if params else 0
        total_len = _CONTAINER_HEADER_SIZE + param_count * 4
        
        self._log("Sending RESPONSE: {} (0x{:04x}), transaction_id={}, params={}",
            response_name, response_code, self._transaction_id, params if params else "None")
        
        # Create container buffer for header + params
        container = bytearray(total_len)
        
        # Create a container header struct
        header = uctypes.struct(uctypes.addressof(container), _MTP_CONTAINER_HEADER_DESC, uctypes.LITTLE_ENDIAN)
        
        # Fill in the container header fields
        header.length = total_len
        header.type = _MTP_CONTAINER_TYPE_RESPONSE
        header.code = response_code
        header.transaction_id = self._transaction_id
        
        # Add parameters if any
        if params:
            for i, param in enumerate(params):
                # Pack parameters directly after header
                struct.pack_into("<I", container, _CONTAINER_HEADER_SIZE + i * 4, param)
        
        # Send the response
        self._tx.write(container)
        self._tx_xfer()
        
        # Clear operation state
        self._current_operation = None
        self._current_params = None
        self._data_expected = False
        self._data_phase_complete = False
        
        return True
    
    def _refresh_object_list(self):
        """Scan the filesystem and rebuild the object handle mapping."""
        self._log("Refreshing object list from storage path: {}", self._storage_path)
        
        # Reset object handles
        self._object_handles = {}
        self._parent_map = {}
        self._next_object_handle = 1
        
        # Start with root directory
        root_handle = self._next_object_handle
        self._next_object_handle += 1
        self._object_handles[root_handle] = self._storage_path
        self._parent_map[root_handle] = 0  # No parent
        self._log("Added root directory with handle {}", root_handle)
        
        # Walk the directory tree
        self._scan_directory(self._storage_path, root_handle)
        
        self._log("Object scan complete, found {} objects", len(self._object_handles))
    
    def _scan_directory(self, path, parent_handle):
        """Recursively scan a directory and add objects to handle maps."""
        try:
            # Ensure path ends with a slash
            if not path.endswith('/'):
                path += '/'
                
            # List all entries in this directory
            entries = os.listdir(path)
            self._log("Scanning directory: {}, found {} entries", path, len(entries))
            
            for entry in entries:
                full_path = path + entry
                
                try:
                    # Get file/directory info
                    stat = os.stat(full_path)
                    is_dir = stat[0] & 0x4000  # S_IFDIR
                    
                    # Create a new handle for this object
                    handle = self._next_object_handle
                    self._next_object_handle += 1
                    
                    # Add to our mappings
                    self._object_handles[handle] = full_path
                    self._parent_map[handle] = parent_handle
                    
                    entry_type = "directory" if is_dir else "file"
                    self._log("Added {} '{}' with handle {}", entry_type, full_path, handle)
                    
                    # Recursively scan subdirectories
                    if is_dir:
                        self._scan_directory(full_path, handle)
                except Exception as e:
                    # Skip entries that cause errors
                    self._log("Error scanning entry '{}': {}", full_path, str(e))
                    continue
        except Exception as e:
            # Log errors during directory scan
            self._log("Error scanning directory '{}': {}", path, str(e))
    
    def _get_format_by_path(self, path):
        """Determine MTP format code based on file extension."""
        lower_path = path.lower()
        
        if lower_path.endswith('/'):
            return _MTP_FORMAT_ASSOCIATION
            
        # Check if it's a directory based on stat
        try:
            stat = os.stat(path)
            if stat[0] & 0x4000:  # S_IFDIR
                return _MTP_FORMAT_ASSOCIATION
        except:
            pass
            
        # Determine format by extension
        if lower_path.endswith('.txt'):
            return _MTP_FORMAT_TEXT
        elif lower_path.endswith('.htm') or lower_path.endswith('.html'):
            return _MTP_FORMAT_HTML
        elif lower_path.endswith('.wav'):
            return _MTP_FORMAT_WAV
        elif lower_path.endswith('.mp3'):
            return _MTP_FORMAT_MP3
        elif lower_path.endswith('.jpg') or lower_path.endswith('.jpeg'):
            return _MTP_FORMAT_EXIF_JPEG
        elif lower_path.endswith('.bmp'):
            return _MTP_FORMAT_BMP
        elif lower_path.endswith('.gif'):
            return _MTP_FORMAT_GIF
        elif lower_path.endswith('.png'):
            return _MTP_FORMAT_PNG
        else:
            return _MTP_FORMAT_UNDEFINED
    
    def _write_mtp_string(self, buffer, offset, string):
        """Write an MTP UTF-16 string to a buffer at the given offset.
        
        Args:
            buffer: Target buffer to write to
            offset: Offset in buffer to start writing
            string: String to encode
            
        Returns:
            Number of bytes written
        """
        if not string:
            # Empty string - just write a 0 length
            struct.pack_into("<B", buffer, offset, 0)
            return 1
            
        start = offset
        
        # String length in 8-bit characters (including null terminator)
        struct.pack_into("<B", buffer, offset, len(string) + 1)
        offset += 1
        
        # String data (each character as 16-bit Unicode)
        # Use little-endian UTF-16 encoding with BOM (Byte Order Mark)
        for c in string:
            # Little-endian format (LE) - character code in first byte, 0 in second byte
            struct.pack_into("<H", buffer, offset, ord(c))
            offset += 2
            
        # Null terminator (16-bit)
        struct.pack_into("<H", buffer, offset, 0)
        offset += 2
        
        # Return total bytes written
        return offset - start # 2 + (len(string) + 1) * 2
    
    def _format_timestamp(self, timestamp):
        """Format a timestamp into MTP date string format."""
        import time
        
        try:
            # Convert timestamp to struct_time
            t = time.localtime(timestamp)
            # Format as YYYYMMDDThhmmss
            return "{:04d}{:02d}{:02d}T{:02d}{:02d}{:02d}".format(
                t[0], t[1], t[2], t[3], t[4], t[5])
        except:
            # Return a default timestamp if conversion fails
            return "20240101T000000"