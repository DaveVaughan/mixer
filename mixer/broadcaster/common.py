from enum import IntEnum
import threading
import select
import socket
import struct
import json
import logging


mutex = threading.RLock()

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 12800

logger = logging.getLogger(__name__)


class MessageType(IntEnum):
    JOIN_ROOM = 1
    CREATE_ROOM = 2
    LEAVE_ROOM = 3
    LIST_ROOMS = 4
    CONTENT = 5
    CLEAR_CONTENT = 6
    DELETE_ROOM = 7
    CLEAR_ROOM = 8
    # All clients that have joined a room
    LIST_ROOM_CLIENTS = 9
    # All joined clients for all rooms
    LIST_CLIENTS = 10
    SET_CLIENT_NAME = 11
    SEND_ERROR = 12
    CONNECTION_LOST = 13
    # All all joined and un joined clients
    LIST_ALL_CLIENTS = 14

    COMMAND = 100
    DELETE = 101
    CAMERA = 102
    LIGHT = 103
    MESHCONNECTION_DEPRECATED = 104
    RENAME = 105
    DUPLICATE = 106
    SEND_TO_TRASH = 107
    RESTORE_FROM_TRASH = 108
    TEXTURE = 109

    ADD_COLLECTION_TO_COLLECTION = 110
    REMOVE_COLLECTION_FROM_COLLECTION = 111
    ADD_OBJECT_TO_COLLECTION = 112
    REMOVE_OBJECT_FROM_COLLECTION = 113

    ADD_OBJECT_TO_SCENE = 114
    ADD_COLLECTION_TO_SCENE = 115

    INSTANCE_COLLECTION = 116
    COLLECTION = 117
    COLLECTION_REMOVED = 118
    SET_SCENE = 119
    GREASE_PENCIL_MESH = 120
    GREASE_PENCIL_MATERIAL = 121
    GREASE_PENCIL_CONNECTION = 122
    GREASE_PENCIL_TIME_OFFSET = 123
    FRAME_START_END = 124
    CAMERA_ANIMATION = 125

    REMOVE_OBJECT_FROM_SCENE = 126
    REMOVE_COLLECTION_FROM_SCENE = 127

    SCENE = 128
    SCENE_REMOVED = 129

    ADD_OBJECT_TO_VRTIST = 130
    OBJECT_VISIBILITY = 131

    # Start / End a group of command. Allows to inform clients that they must process multiple commands
    # before giving back control to they users.
    GROUP_BEGIN = 132
    GROUP_END = 133

    SCENE_RENAMED = 134

    ADD_KEYFRAME = 135
    REMOVE_KEYFRAME = 136
    QUERY_CURRENT_FRAME = 137
    QUERY_OBJECT_DATA = 138

    OPTIMIZED_COMMANDS = 200
    TRANSFORM = 201
    MESH = 202
    MATERIAL = 203
    FRAME = 204
    PLAY = 205
    PAUSE = 206


class LightType(IntEnum):
    SPOT = 0  # directly mapped from Unity enum
    SUN = 1
    POINT = 2


class SensorFitMode(IntEnum):
    AUTO = 0
    VERTICAL = 1
    HORIZONTAL = 2


class ClientDisconnectedException(Exception):
    """When a client is disconnected and we try to read from it."""


def int_to_bytes(value, size=8):
    return value.to_bytes(size, byteorder="little")


def bytes_to_int(value):
    return int.from_bytes(value, "little")


def int_to_message_type(value):
    return MessageType(value)


def encode_bool(value):
    if value:
        return int_to_bytes(1, 4)
    else:
        return int_to_bytes(0, 4)


def decode_bool(data, index):
    value = bytes_to_int(data[index : index + 4])
    if value == 1:
        return True, index + 4
    else:
        return False, index + 4


def encode_string(value):
    encoded_value = value.encode()
    return int_to_bytes(len(encoded_value), 4) + encoded_value


def decode_string(data, index):
    string_length = bytes_to_int(data[index : index + 4])
    start = index + 4
    end = start + string_length
    value = data[start:end].decode()
    return value, end


def encode_json(value: dict):
    return encode_string(json.dumps(value))


def decode_json(data, index):
    value, end = decode_string(data, index)
    return json.loads(value), end


def encode_float(value):
    return struct.pack("f", value)


def decode_float(data, index):
    return struct.unpack("f", data[index : index + 4])[0], index + 4


def encode_int(value):
    return struct.pack("i", value)


def decode_int(data, index):
    return struct.unpack("i", data[index : index + 4])[0], index + 4


def encode_vector2(value):
    return struct.pack("2f", *(value.x, value.y))


def decode_vector2(data, index):
    return struct.unpack("2f", data[index : index + 2 * 4]), index + 2 * 4


def encode_vector3(value):
    return struct.pack("3f", *(value.x, value.y, value.z))


def decode_vector3(data, index):
    return struct.unpack("3f", data[index : index + 3 * 4]), index + 3 * 4


def encode_vector4(value):
    return struct.pack("4f", *(value[0], value[1], value[2], value[3]))


def decode_vector4(data, index):
    return struct.unpack("4f", data[index : index + 4 * 4]), index + 4 * 4


def encode_matrix(value):
    return (
        encode_vector4(value.col[0])
        + encode_vector4(value.col[1])
        + encode_vector4(value.col[2])
        + encode_vector4(value.col[3])
    )


def decode_matrix(data, index):
    c0, index = decode_vector4(data, index)
    c1, index = decode_vector4(data, index)
    c2, index = decode_vector4(data, index)
    c3, index = decode_vector4(data, index)
    return (c0, c1, c2, c3), index


def encode_color(value):
    if len(value) == 3:
        return struct.pack("4f", *(value[0], value[1], value[2], 1.0))
    else:
        return struct.pack("4f", *(value[0], value[1], value[2], value[3]))


def decode_color(data, index):
    return struct.unpack("4f", data[index : index + 4 * 4]), index + 4 * 4


def encode_quaternion(value):
    return struct.pack("4f", *(value.w, value.x, value.y, value.z))


def decode_quaternion(data, index):
    return struct.unpack("4f", data[index : index + 4 * 4]), index + 4 * 4


def encode_string_array(values):
    buffer = encode_int(len(values))
    for item in values:
        buffer += encode_string(item)
    return buffer


def decode_string_array(data, index):
    count = bytes_to_int(data[index : index + 4])
    index = index + 4
    values = []
    for _ in range(count):
        string, index = decode_string(data, index)
        values.append(string)
    return values, index


def decode_array(data, index, schema, inc):
    count = bytes_to_int(data[index : index + 4])
    start = index + 4
    end = start
    values = []
    for _ in range(count):
        end = start + inc
        values.append(struct.unpack(schema, data[start:end]))
        start = end
    return values, end


def decode_float_array(data, index):
    return decode_array(data, index, "f", 4)


def decode_int_array(data, index):
    count = bytes_to_int(data[index : index + 4])
    start = index + 4
    values = []
    for _ in range(count):
        end = start + 4
        values.extend(struct.unpack("I", data[start:end]))
        start = end
    return values, end


def decode_int2_array(data, index):
    return decode_array(data, index, "2I", 2 * 4)


def decode_int3_array(data, index):
    return decode_array(data, index, "3I", 3 * 4)


def decode_vector3_array(data, index):
    return decode_array(data, index, "3f", 3 * 4)


def decode_vector2_array(data, index):
    return decode_array(data, index, "2f", 2 * 4)


class Command:
    _id = 100

    def __init__(self, command_type: MessageType, data=b"", command_id=0):
        self.data = data or b""
        self.type = command_type
        self.id = command_id
        if command_id == 0:
            self.id = Command._id
            Command._id += 1


class CommandFormatter:
    def format_clients(self, clients):
        s = ""
        for c in clients:
            s += f'   - {c["ip"]}:{c["port"]} name = "{c["name"]}" room = "{c["room"]}"\n'
        return s

    def format(self, command: Command):

        s = f"={command.type.name}: "

        if command.type == MessageType.LIST_ROOMS:
            rooms, _ = decode_string_array(command.data, 0)
            s += "LIST_ROOMS: "
            if len(rooms) == 0:
                s += "  No rooms"
            else:
                s += f" {len(rooms)} room(s) : {rooms}"
        elif command.type == MessageType.LIST_ROOM_CLIENTS:
            clients, _ = decode_json(command.data, 0)
            if len(clients) == 0:
                s += f"  No clients in room"
            else:
                s += f"  {len(clients)} client(s) in room :\n"
                s += self.format_clients(clients)
        elif command.type == MessageType.LIST_CLIENTS or command.type == MessageType.LIST_ALL_CLIENTS:
            clients, _ = decode_json(command.data, 0)
            if len(clients) == 0:
                s += "  No clients\n"
            else:
                s += f"  {len(clients)} client(s):\n"
                s += self.format_clients(clients)
        elif command.type == MessageType.CONNECTION_LOST:
            s += "CONNECTION_LOST:\n"
        elif command.type == MessageType.SEND_ERROR:
            s += f"ERROR: {decode_string(command.data, 0)[0]}\n"
        else:
            pass

        return s


def recv(socket: socket.socket, size: int):
    result = b""
    while size != 0:
        r, _, _ = select.select([socket], [], [], 0.1)
        if len(r) > 0:
            try:
                tmp = socket.recv(size)
            except (ConnectionAbortedError, ConnectionResetError) as e:
                logger.warning(e)
                raise ClientDisconnectedException()

            if len(tmp) == 0:
                raise ClientDisconnectedException()

            result += tmp
            size -= len(tmp)
    return result


def read_message(socket: socket.socket) -> Command:
    if not socket:
        logger.warning("read_message called with no socket")
        return None

    r, _, _ = select.select([socket], [], [], 0.0001)
    if len(r) == 0:
        return None

    try:
        prefix_size = 14
        msg = recv(socket, prefix_size)

        frame_size = bytes_to_int(msg[:8])
        command_id = bytes_to_int(msg[8:12])
        message_type = bytes_to_int(msg[12:])

        msg = recv(socket, frame_size)

        return Command(int_to_message_type(message_type), msg, command_id)

    except ClientDisconnectedException:
        raise
    except Exception as e:
        logger.error(e, exc_info=True)
        raise


def write_message(sock: socket.socket, command: Command):
    if not sock:
        logger.warning("write_message called with no socket")
        return

    size = int_to_bytes(len(command.data), 8)
    command_id = int_to_bytes(command.id, 4)
    mtype = int_to_bytes(command.type.value, 2)

    buffer = size + command_id + mtype + command.data

    try:
        _, w, _ = select.select([], [sock], [])
        if sock.sendall(buffer) is not None:
            raise ClientDisconnectedException()
    except (ConnectionAbortedError, ConnectionResetError) as e:
        logger.warning(e)
        raise ClientDisconnectedException()