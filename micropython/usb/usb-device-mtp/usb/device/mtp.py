# MicroPython USB MTP module
# MIT license; Copyright (c) 2024 MicroPython Developers
from micropython import const, schedule
import struct
import time
import os
import io
import errno

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
_MAX_PACKET_SIZE = const(512)
_DEFAULT_TX_BUF_SIZE = const(4096)
_DEFAULT_RX_BUF_SIZE = const(4096)
_CONTAINER_HEADER_SIZE = const(12)


class MTPInterface(Interface):
    """USB MTP device interface for MicroPython.
    
    This class implements a basic MTP (Media Transfer Protocol) interface
    that allows USB hosts to access the MicroPython filesystem.
    """
    
    def __init__(self, storage_path="/", rx_size=_DEFAULT_RX_BUF_SIZE, tx_size=_DEFAULT_TX_BUF_SIZE):
        """Initialize the MTP interface.
        
        Args:
            storage_path: Root path to expose via MTP (default: "/")
            rx_size: Size of the receive buffer in bytes
            tx_size: Size of the transmit buffer in bytes
        """
        super().__init__()
        
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
        self._storage_id = 0x00010001  # Fixed ID for the single storage we support
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
        
    def desc_cfg(self, desc, itf_num, ep_num, strs):
        """Build the USB configuration descriptor for this interface."""
        # Add the interface descriptor for MTP (PIMA 15740 Still Image)
        desc.interface(
            itf_num, 
            3,  # Number of endpoints (1 bulk IN, 1 bulk OUT, 1 interrupt IN)
            _INTERFACE_CLASS_STILL_IMAGE,
            _INTERFACE_SUBCLASS_STILL_IMAGE,
            _INTERFACE_PROTOCOL_PIMA_15740,
            0,  # No string descriptor
        )
        
        # Add the endpoints (bulk OUT, bulk IN, interrupt IN)
        self.ep_out = ep_num
        self.ep_in = ep_num | _EP_IN_FLAG
        self.ep_intr = (ep_num + 1) | _EP_IN_FLAG
        
        desc.endpoint(self.ep_out, "bulk", _MAX_PACKET_SIZE, 0)
        desc.endpoint(self.ep_in, "bulk", _MAX_PACKET_SIZE, 0)
        desc.endpoint(self.ep_intr, "interrupt", 8, 10)  # 10ms interval for events
    
    def num_eps(self):
        """Return the number of endpoints used by this interface."""
        return 2  # We use 2 endpoint numbers (3 endpoints total with IN flag)
    
    def on_open(self):
        """Called when the USB host configures the device."""
        super().on_open()
        # Start transfers for receiving commands and data
        self._rx_xfer()
        
    def on_reset(self):
        """Called when the USB device is reset by the host."""
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
    
    def _rx_xfer(self):
        """Submit a new transfer to receive data from the host."""
        if self.is_open() and not self.xfer_pending(self.ep_out) and self._rx.writable():
            self.submit_xfer(self.ep_out, self._rx.pend_write(), self._rx_cb)
    
    def _rx_cb(self, ep, res, num_bytes):
        """Callback when data is received from the host."""
        if res == 0:
            self._rx.finish_write(num_bytes)
            schedule(self._process_rx, None)
        self._rx_xfer()  # Continue receiving
    
    def _tx_xfer(self):
        """Submit a new transfer to send data to the host."""
        if self.is_open() and not self.xfer_pending(self.ep_in) and self._tx.readable():
            self.submit_xfer(self.ep_in, self._tx.pend_read(), self._tx_cb)
    
    def _tx_cb(self, ep, res, num_bytes):
        """Callback when data has been sent to the host."""
        if res == 0:
            self._tx.finish_read(num_bytes)
        self._tx_xfer()  # Send more data if available
    
    def _process_rx(self, _):
        """Process received data from the host."""
        # Check if there's enough data for a container header
        if self._rx.readable() < _CONTAINER_HEADER_SIZE:
            return
        
        # Peek at the container header without consuming it yet
        header = self._rx.pend_read()
        
        # Parse container header
        length = struct.unpack_from("<I", header, 0)[0]
        container_type = struct.unpack_from("<H", header, 4)[0]
        code = struct.unpack_from("<H", header, 6)[0]
        transaction_id = struct.unpack_from("<I", header, 8)[0]
        
        # Ensure we have the complete container
        if self._rx.readable() < length:
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
                self._send_response(_MTP_RESPONSE_GENERAL_ERROR)
                return
            
            # Process the data phase
            data_size = length - _CONTAINER_HEADER_SIZE
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
                
                self._process_data_phase(data)
            else:
                # Not enough data received
                # Skip incomplete data
                self._rx.finish_read(self._rx.readable())
                self._send_response(_MTP_RESPONSE_INCOMPLETE_TRANSFER)
    
    def _handle_command(self):
        """Process an MTP command based on the current operation code."""
        op = self._current_operation
        params = self._current_params
        
        # Check if session is open (required for most operations)
        if not self._session_open and op != _MTP_OPERATION_OPEN_SESSION and op != _MTP_OPERATION_GET_DEVICE_INFO:
            self._send_response(_MTP_RESPONSE_SESSION_NOT_OPEN)
            return
        
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
        # Prepare the device info dataset
        data = bytearray(512)  # Pre-allocate buffer
        offset = 0
        
        # Standard version
        struct.pack_into("<H", data, offset, 100)  # Version 1.00
        offset += 2
        
        # MTP vendor extension ID
        struct.pack_into("<I", data, offset, 0x00000000)  # No vendor extension
        offset += 4
        
        # MTP version
        struct.pack_into("<H", data, offset, 100)  # Version 1.00
        offset += 2
        
        # MTP extensions (empty string)
        struct.pack_into("<H", data, offset, 0)  # No extension string
        offset += 2
        
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
        struct.pack_into("<H", data, offset, len(operations))
        offset += 2
        for op in operations:
            struct.pack_into("<H", data, offset, op)
            offset += 2
            
        # Supported events (array of event codes) - empty for now
        struct.pack_into("<H", data, offset, 0)  # No events supported
        offset += 2
        
        # Supported device properties - empty for now
        struct.pack_into("<H", data, offset, 0)  # No device properties
        offset += 2
        
        # Supported capture formats - empty for now
        struct.pack_into("<H", data, offset, 0)  # No capture formats
        offset += 2
        
        # Supported playback formats (file formats we support)
        formats = [
            _MTP_FORMAT_ASSOCIATION,  # directories
            _MTP_FORMAT_TEXT,         # text files
            _MTP_FORMAT_UNDEFINED     # all other files
        ]
        struct.pack_into("<H", data, offset, len(formats))
        offset += 2
        for fmt in formats:
            struct.pack_into("<H", data, offset, fmt)
            offset += 2
        
        # Manufacturer (empty string)
        struct.pack_into("<H", data, offset, 0)
        offset += 2
        
        # Model (MicroPython)
        model = "MicroPython"
        struct.pack_into("<H", data, offset, len(model) + 1)
        offset += 2
        for c in model:
            struct.pack_into("<H", data, offset, ord(c))
            offset += 2
        struct.pack_into("<H", data, offset, 0)  # Null terminator
        offset += 2
        
        # Device version (empty string)
        struct.pack_into("<H", data, offset, 0)
        offset += 2
        
        # Serial number (empty string)
        struct.pack_into("<H", data, offset, 0)
        offset += 2
        
        # Send the device info
        self._send_data(data[:offset])
        
        # Then send success response
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_open_session(self, params):
        """Handle OpenSession command."""
        if not params:
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
            return
            
        session_id = params[0]
        
        if session_id == 0:
            self._send_response(_MTP_RESPONSE_INVALID_PARAMETER)
        elif self._session_open:
            self._send_response(_MTP_RESPONSE_SESSION_ALREADY_OPEN)
        else:
            self._session_open = True
            self._session_id = session_id
            self._send_response(_MTP_RESPONSE_OK)
            # Refresh the object list when opening a session
            self._refresh_object_list()
    
    def _cmd_close_session(self):
        """Handle CloseSession command."""
        self._session_open = False
        self._session_id = 0
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_get_storage_ids(self):
        """Handle GetStorageIDs command."""
        # We only support a single storage
        data = bytearray(12)
        struct.pack_into("<II", data, 0, 1, self._storage_id)  # Count=1, ID=storage_id
        self._send_data(data)
        self._send_response(_MTP_RESPONSE_OK)
    
    def _cmd_get_storage_info(self, params):
        """Handle GetStorageInfo command."""
        if not params or params[0] != self._storage_id:
            self._send_response(_MTP_RESPONSE_INVALID_STORAGE_ID)
            return
            
        # Get storage capacity information
        try:
            fs_stat = os.statvfs(self._storage_path)
            free_bytes = fs_stat[0] * fs_stat[4]  # f_bsize * f_bavail
            total_bytes = fs_stat[0] * fs_stat[2]  # f_bsize * f_blocks
        except:
            # If we can't get stats, just return reasonable defaults
            free_bytes = 1024 * 1024  # 1MB
            total_bytes = 4 * 1024 * 1024  # 4MB
        
        # Prepare storage info dataset
        data = bytearray(128)
        offset = 0
        
        # Storage type
        struct.pack_into("<H", data, offset, _MTP_STORAGE_FIXED_RAM)
        offset += 2
        
        # Filesystem type
        struct.pack_into("<H", data, offset, 0x0002)  # Generic hierarchical
        offset += 2
        
        # Access capability
        struct.pack_into("<H", data, offset, _MTP_STORAGE_READ_WRITE)
        offset += 2
        
        # Max capacity
        struct.pack_into("<Q", data, offset, total_bytes)
        offset += 8
        
        # Free space
        struct.pack_into("<Q", data, offset, free_bytes)
        offset += 8
        
        # Free space in objects (unknown - use 0xFFFFFFFF)
        struct.pack_into("<I", data, offset, 0xFFFFFFFF)
        offset += 4
        
        # Storage description (empty)
        struct.pack_into("<H", data, offset, 0)
        offset += 2
        
        # Volume identifier (root)
        volume_id = "MicroPython"
        struct.pack_into("<H", data, offset, len(volume_id) + 1)
        offset += 2
        for c in volume_id:
            struct.pack_into("<H", data, offset, ord(c))
            offset += 2
        struct.pack_into("<H", data, offset, 0)  # Null terminator
        offset += 2
        
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
            self._send_response(_MTP_RESPONSE_INVALID_STORAGE_ID)
            return
            
        # Collect filtered handles
        handles = []
        for handle, parent in self._parent_map.items():
            if (parent_handle == 0 or parent == parent_handle) and handle in self._object_handles:
                # Apply format filter if specified
                if format_code == 0 or self._get_format_by_path(self._object_handles[handle]) == format_code:
                    handles.append(handle)
        
        # Prepare and send the array of handles
        data = bytearray(4 + len(handles) * 4)
        struct.pack_into("<I", data, 0, len(handles))  # Count
        for i, handle in enumerate(handles):
            struct.pack_into("<I", data, 4 + i*4, handle)
        
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
        
        # Object size (in bytes)
        struct.pack_into("<I", data, offset, filesize)
        offset += 4
        
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
        struct.pack_into("<H", data, offset, len(filename) + 1)  # String length including null
        offset += 2
        for c in filename:
            struct.pack_into("<H", data, offset, ord(c))
            offset += 2
        struct.pack_into("<H", data, offset, 0)  # Null terminator
        offset += 2
        
        # Date created (as string) - format: YYYYMMDDThhmmss
        dt_str = self._format_timestamp(ctime)
        struct.pack_into("<H", data, offset, len(dt_str) + 1)
        offset += 2
        for c in dt_str:
            struct.pack_into("<H", data, offset, ord(c))
            offset += 2
        struct.pack_into("<H", data, offset, 0)  # Null terminator
        offset += 2
        
        # Date modified (as string)
        dt_str = self._format_timestamp(mtime)
        struct.pack_into("<H", data, offset, len(dt_str) + 1)
        offset += 2
        for c in dt_str:
            struct.pack_into("<H", data, offset, ord(c))
            offset += 2
        struct.pack_into("<H", data, offset, 0)  # Null terminator
        offset += 2
        
        # Keywords (empty string)
        struct.pack_into("<H", data, offset, 0)
        offset += 2
        
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
                
            # Open the file and prepare to send it
            with open(filepath, "rb") as f:
                # Send the file in chunks to avoid large memory allocations
                while True:
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    
                    # We're sending multiple data packets here - this is okay for bulk endpoints
                    self._send_data(chunk, final=False)
                
                # Send an empty packet if needed to finish the transfer
                self._send_data(b"", final=True)
                
                # Send the response
                self._send_response(_MTP_RESPONSE_OK)
                
        except OSError as e:
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
                # Only delete empty directories
                try:
                    os.rmdir(filepath)
                except OSError as e:
                    if e.errno == errno.ENOTEMPTY:
                        self._send_response(_MTP_RESPONSE_PARTIAL_DELETION)
                        return
                    else:
                        raise
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
            return False
            
        # Create container header
        container = bytearray(_CONTAINER_HEADER_SIZE)
        total_len = _CONTAINER_HEADER_SIZE + len(data)
        
        struct.pack_into("<IHHI", container, 0, 
                         total_len,                    # Container length
                         _MTP_CONTAINER_TYPE_DATA,     # Container type
                         self._current_operation,      # Operation code
                         self._transaction_id)         # Transaction ID
        
        # Send header
        self._tx.write(container)
        
        # Send data
        if data:
            self._tx.write(data)
            
        # Start transfer
        self._tx_xfer()
        
        return True
    
    def _send_response(self, response_code, params=None):
        """Send response phase of an MTP transaction."""
        if not self.is_open():
            return False
            
        # Calculate response length
        param_count = len(params) if params else 0
        total_len = _CONTAINER_HEADER_SIZE + param_count * 4
        
        # Create and fill container header
        container = bytearray(total_len)
        struct.pack_into("<IHHI", container, 0,
                         total_len,                    # Container length
                         _MTP_CONTAINER_TYPE_RESPONSE, # Container type
                         response_code,                # Response code
                         self._transaction_id)         # Transaction ID
        
        # Add parameters if any
        if params:
            for i, param in enumerate(params):
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
        # Reset object handles
        self._object_handles = {}
        self._parent_map = {}
        self._next_object_handle = 1
        
        # Start with root directory
        root_handle = self._next_object_handle
        self._next_object_handle += 1
        self._object_handles[root_handle] = self._storage_path
        self._parent_map[root_handle] = 0  # No parent
        
        # Walk the directory tree
        self._scan_directory(self._storage_path, root_handle)
    
    def _scan_directory(self, path, parent_handle):
        """Recursively scan a directory and add objects to handle maps."""
        try:
            # Ensure path ends with a slash
            if not path.endswith('/'):
                path += '/'
                
            # List all entries in this directory
            entries = os.listdir(path)
            
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
                    
                    # Recursively scan subdirectories
                    if is_dir:
                        self._scan_directory(full_path, handle)
                except:
                    # Skip entries that cause errors
                    continue
        except:
            # Ignore errors during directory scan
            pass
    
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