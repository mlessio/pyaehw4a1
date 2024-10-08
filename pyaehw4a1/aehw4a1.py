import sys
import asyncio
import ipaddress

import ifaddr

from .commands import ReadCommand
from .commands import UpdateCommand
from .responses import ResponsePacket
from .responses import DataPacket
from .exceptions import *


MAX_NUMBER_WORKERS = 200


class AehW4a1:
    def __init__(self, host=None):
        if host is None:
            self._host = None
        else:
            self._host = host

    async def check(self):
        if not self._host:
            raise ConnectionError("Host required")
        
        try:
            ipaddress.IPv4Network(self._host)
        except ValueError:
            raise ConnectionError(f"Invalid IP address: {self._host}") from None
        
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, 8888), timeout = 2)
        except:
            raise ConnectionError(f"AC unavailable at {self._host}") from None

        writer.write(bytes("AT+XMV", 'utf-8'))
        await writer.drain()
        data = await reader.readline()
        writer.close()
        await writer.wait_closed()
        if bytes("+XMV:", 'utf-8') in data:
            return True
            
        raise ConnectionError(f"Unknown device {self._host}")

    async def version(self):
        if not self._host:
            raise ConnectionError("Host required")
        
        try:
            ipaddress.IPv4Network(self._host)
        except ValueError:
            raise ConnectionError(f"Invalid IP address: {self._host}") from None
        
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, 8888), timeout = 2)
        except:
            raise ConnectionError(f"AC unavailable at {self._host}") from None

        writer.write(bytes("AT+XMV", 'utf-8'))
        await writer.drain()
        data = await reader.readline()
        writer.close()
        await writer.wait_closed()
        if bytes("+XMV:", 'utf-8') in data:
            return data
            
        raise ConnectionError(f"Unknown device {self._host}")

    async def command(self, command):
        if not self._host:
            raise ConnectionError("Host required")
        
        try:
            ipaddress.IPv4Network(self._host)
        except ValueError:
            raise ConnectionError(f"Invalid IP address: {self._host}") from None
            
        for name, member in ReadCommand.__members__.items():
            if command == name:
                return await self._read_command(member)
        for name, member in UpdateCommand.__members__.items():
            if command == name:
                if command == "temp_to_F":
                    await self._update_command(member)
                    return await self.command("temp_to_F_reset_temp")
                elif command == "temp_to_C":
                    await self._update_command(member)
                    return await self.command("temp_to_C_reset_temp")
                else:
                    return await self._update_command(member)

        raise UnkCmdError(f"Not yet implemented: {command}")

    async def _update_command(self, command):
        pure_bytes = await self._send_recv_packet(command)
        packet_type = await self._packet_type(pure_bytes)
        if (await self._check_response(packet_type, pure_bytes)):
            return True

        raise UnkPacketError(
            f"Unknown packet type {packet_type}: {pure_bytes.hex()}"
            )

    async def _read_command(self, command):
        pure_bytes = await self._send_recv_packet(command)
        packet_type = await self._packet_type(pure_bytes)
        data_start_pos = await self._check_response(packet_type, pure_bytes)
        if data_start_pos:
            result = await self._bits_value(packet_type, pure_bytes, data_start_pos)
            return result

        raise UnkPacketError(
            f"Unknown packet type {packet_type}: {pure_bytes.hex()}"
            )

    async def _send_recv_packet(self, command):
        for i in range(5):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, 8888), timeout = 2)
            except:
                pass
            else:
                break
        else:
            raise ConnectionError(f"AC unavailable") from None
        
        for i in range(5):
            writer.write(command.value)
            await writer.drain()
            try:
                data = await asyncio.wait_for(reader.read(100), timeout = 2)
                writer.close()
                await writer.wait_closed()
            except:
                pass
            else:
                break
        else:
            raise ConnectionError(f"AC does not respond") from None

        return data

    async def _bits_value(self, packet_type, pure_bytes, data_pos):
        result = {}
        binary_string = f"{int(pure_bytes.hex(),16):08b}"
        binary_data = binary_string[data_pos*8:-24]
        for data_packet in DataPacket:
            if packet_type in data_packet.name:
                for field in data_packet.value:
                    result[field.name] = binary_data[(field.offset - 1):
                                        (field.offset + field.length - 1)]
                return result

        raise UnkDataError(f"Unknown data type {packet_type}: {binary_data}")

    async def _packet_type(self, string):
        type = int(string[13:14].hex(),16)
        sub_type = int(string[14:15].hex(),16)
        result = f"{type}_{sub_type}"
        return result

    async def _check_response(self, packet_type, pure_bytes):
        for response_packet in ResponsePacket:
            if packet_type in response_packet.name:
                if response_packet.value not in pure_bytes:

                    raise WrongRespError(
                        f"Wrong response for type {packet_type}: {pure_bytes.hex()}"
                        )

                return len(response_packet.value)
        return False

    async def discovery(self, full=None):
        if full is None:
            self._full = None
        elif full == True:
            self._full = True
        else:
            raise WrongArgError("Optional argument for discovery is: True")

        nets = []
        adapters = ifaddr.get_adapters()
        for adapter in adapters:
            for ip in adapter.ips:
                if ip.is_IPv4 and ip.ip != "127.0.0.1":
                    if self._full:
                        nets.append(
                            ipaddress.IPv4Network(f"{ip.ip}/{ip.network_prefix}",
                            strict=False)
                        )
                    else:
                        nets.append(
                            ipaddress.IPv4Network(f"{ip.ip}/24", strict=False)
                        )
        if not nets:        
            raise NoNetworksError("No networks available")

        acs = []
        out_queue = asyncio.Queue()
        for net in nets:
            task_queue = asyncio.Queue()
            scan_completed = asyncio.Event()
            scan_completed.clear()
            
            for ip in net:
                print(str(ip))
                await task_queue.put(str(ip))

            tasks = []

            for _ in range(MAX_NUMBER_WORKERS):
                tasks.append(asyncio.create_task(self._task_worker(task_queue, out_queue)))         
            await task_queue.join()
            print(task_queue.qsize())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if out_queue.qsize() and not self._full:
                break

        while out_queue.qsize():
            acs.append(out_queue.get_nowait())
        return acs


    async def _task_worker(self, task_queue, out_queue):
        while True:
            ip = (await task_queue.get())
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, 8888), timeout = 1)
            except:
                pass
            else:
                writer.write(bytes("AT+XMV", 'utf-8'))
                await writer.drain()
                data = await reader.readline()
                writer.close()
                await writer.wait_closed()
                if bytes("+XMV:", 'utf-8') in data:
                    out_queue.put_nowait(ip)
            finally:
                print("done")
                task_queue.task_done()
                print(task_queue.qsize())
