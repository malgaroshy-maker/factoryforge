using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace NetToCXSim.Services
{
    /// <summary>
    /// Omron FINS TCP &amp; UDP Server Bridge for CX-Simulator.
    /// Enables external clients (Weintek EasyBuilder Pro, SCADA, Node-RED, IoTClient)
    /// to connect to localhost:9600 or PC LAN IP and read/write live simulator memory.
    /// </summary>
    public class OmronFinsServer : IDisposable
    {
        private readonly OmronSimulatorEngine _simEngine;
        private int _port;
        private readonly int _basePort;
        private readonly string _ip;

        private TcpListener _tcpListener;
        private UdpClient _udpListener;
        private CancellationTokenSource _cts;

        private readonly List<Socket> _activeTcpClients = new List<Socket>();
        private readonly object _clientsLock = new object();

        public event Action<string> OnLog;
        public int Port => _port;
        public bool IsRunning { get; private set; }
        public int ClientCount
        {
            get
            {
                lock (_clientsLock)
                {
                    return _activeTcpClients.Count;
                }
            }
        }

        public OmronFinsServer(OmronSimulatorEngine simEngine, int port = 9600, string ip = null)
        {
            _simEngine = simEngine;
            _basePort = port;
            _port = port;
            _ip = ip;
        }

        public void Start()
        {
            if (IsRunning) return;

            _cts = new CancellationTokenSource();
            IPAddress bindIp = string.IsNullOrWhiteSpace(_ip) ? IPAddress.Any : IPAddress.Parse(_ip);

            const int maxPortAttempts = 100;
            int startPort = _basePort > 0 ? _basePort : 9600;
            bool started = false;

            for (int i = 0; i < maxPortAttempts; i++)
            {
                int testPort = startPort + i;
                TcpListener testTcp = null;
                UdpClient testUdp = null;

                try
                {
                    // 1. Try starting TCP Server for FINS/TCP
                    testTcp = new TcpListener(bindIp, testPort);
                    testTcp.Start();

                    // 2. Try starting UDP Server for FINS/UDP
                    testUdp = new UdpClient(new IPEndPoint(bindIp, testPort));

                    // Both successfully bound without exception!
                    _tcpListener = testTcp;
                    _udpListener = testUdp;
                    _port = testPort;
                    started = true;
                    break;
                }
                catch (SocketException ex)
                {
                    try { testTcp?.Stop(); } catch { }
                    try { testUdp?.Close(); } catch { }

                    Log($"[FINS BRIDGE] Port {testPort} is already in use ({ex.SocketErrorCode}). Trying port {testPort + 1}...");
                }
                catch (Exception ex)
                {
                    try { testTcp?.Stop(); } catch { }
                    try { testUdp?.Close(); } catch { }

                    Log($"[FINS BRIDGE] Port {testPort} error: {ex.Message}. Trying port {testPort + 1}...");
                }
            }

            if (started)
            {
                Task.Run(() => AcceptTcpClientsAsync(_tcpListener, _cts.Token));
                Task.Run(() => ReceiveUdpPacketsAsync(_udpListener, _cts.Token));
                IsRunning = true;
                Log($"[FINS BRIDGE] Server listening on {bindIp}:{_port} (TCP & UDP ready for EasyBuilder Pro / SCADA)");
            }
            else
            {
                Log($"[FINS BRIDGE ERROR] Failed to open FINS Server port between {startPort} and {startPort + maxPortAttempts - 1}.");
            }
        }

        public void Stop()
        {
            if (!IsRunning) return;
            IsRunning = false;

            try
            {
                _cts?.Cancel();

                _tcpListener?.Stop();
                _udpListener?.Close();

                lock (_clientsLock)
                {
                    foreach (var client in _activeTcpClients)
                    {
                        try
                        {
                            client.Shutdown(SocketShutdown.Both);
                            client.Close();
                        }
                        catch { }
                    }
                    _activeTcpClients.Clear();
                }

                Log("[FINS BRIDGE] Server stopped.");
            }
            catch (Exception ex)
            {
                Log($"[FINS BRIDGE] Error stopping server: {ex.Message}");
            }
        }

        #region TCP Handling
        private async Task AcceptTcpClientsAsync(TcpListener listener, CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    Socket client = await listener.AcceptSocketAsync();
                    lock (_clientsLock)
                    {
                        _activeTcpClients.Add(client);
                    }

                    string remoteEndPoint = client.RemoteEndPoint?.ToString() ?? "Unknown";
                    Log($"[FINS TCP] Client connected: {remoteEndPoint}");

                    _ = Task.Run(() => ProcessTcpClientAsync(client, token));
                }
                catch (ObjectDisposedException) { break; }
                catch (Exception ex)
                {
                    if (token.IsCancellationRequested) break;
                    Log($"[FINS TCP] Accept error: {ex.Message}");
                }
            }
        }

        private async Task ProcessTcpClientAsync(Socket client, CancellationToken token)
        {
            string ep = client.RemoteEndPoint?.ToString() ?? "Client";
            byte assignedClientNode = 10;
            byte serverNode = 1;

            try
            {
                byte[] headerBuffer = new byte[16];

                while (client.Connected && !token.IsCancellationRequested)
                {
                    // Read 16-byte FINS TCP header
                    int read = await ReadExactAsync(client, headerBuffer, 0, 16, token);
                    if (read < 16) break;

                    // Verify "FINS" magic
                    if (headerBuffer[0] != 0x46 || headerBuffer[1] != 0x49 || headerBuffer[2] != 0x4E || headerBuffer[3] != 0x53)
                    {
                        Log($"[FINS TCP] Invalid header magic received from {ep}");
                        break;
                    }

                    int length = (headerBuffer[4] << 24) | (headerBuffer[5] << 16) | (headerBuffer[6] << 8) | headerBuffer[7];
                    uint command = (uint)((headerBuffer[8] << 24) | (headerBuffer[9] << 16) | (headerBuffer[10] << 8) | headerBuffer[11]);
                    uint errorCode = (uint)((headerBuffer[12] << 24) | (headerBuffer[13] << 16) | (headerBuffer[14] << 8) | headerBuffer[15]);

                    // Read remaining payload
                    int payloadLength = length - 8; // length includes command (4) and error (4)
                    if (payloadLength < 0 || payloadLength > 65536) break;

                    byte[] payload = new byte[payloadLength];
                    if (payloadLength > 0)
                    {
                        int pRead = await ReadExactAsync(client, payload, 0, payloadLength, token);
                        if (pRead < payloadLength) break;
                    }

                    // Handle FINS TCP Commands
                    if (command == 0x00000000)
                    {
                        // 1. Client Node Connection Request
                        if (payload.Length >= 4)
                        {
                            byte requestedNode = payload[3];
                            if (requestedNode != 0) assignedClientNode = requestedNode;
                        }

                        // Response: Command 0x00000001, Error 0, Client Node (4 bytes), Server Node (4 bytes)
                        byte[] resp = new byte[24];
                        resp[0] = 0x46; resp[1] = 0x49; resp[2] = 0x4E; resp[3] = 0x53; // FINS
                        SetBigEndian32(resp, 4, 16); // 16 bytes following length
                        SetBigEndian32(resp, 8, 1);  // Command: 1 (Server Node Response)
                        SetBigEndian32(resp, 12, 0); // Error: 0 (Normal)
                        SetBigEndian32(resp, 16, assignedClientNode);
                        SetBigEndian32(resp, 20, serverNode);

                        client.Send(resp);
                        Log($"[FINS TCP] Handshake with {ep} OK. Client Node: {assignedClientNode}, Server Node: {serverNode}");
                    }
                    else if (command == 0x00000002)
                    {
                        // 2. FINS Frame Send
                        byte[] finsResponse = ProcessFinsFrame(payload, ep, assignedClientNode);
                        if (finsResponse != null)
                        {
                            byte[] resp = new byte[16 + finsResponse.Length];
                            resp[0] = 0x46; resp[1] = 0x49; resp[2] = 0x4E; resp[3] = 0x53;
                            SetBigEndian32(resp, 4, 8 + finsResponse.Length);
                            SetBigEndian32(resp, 8, 2); // Command: 2 (FINS Frame)
                            SetBigEndian32(resp, 12, 0); // Error: 0
                            Buffer.BlockCopy(finsResponse, 0, resp, 16, finsResponse.Length);

                            client.Send(resp);
                        }
                    }
                    else
                    {
                        Log($"[FINS TCP] Unknown command 0x{command:X8} from {ep}");
                    }
                }
            }
            catch (Exception ex)
            {
                if (!token.IsCancellationRequested)
                {
                    Log($"[FINS TCP] Client {ep} disconnected: {ex.Message}");
                }
            }
            finally
            {
                lock (_clientsLock)
                {
                    _activeTcpClients.Remove(client);
                }
                try { client.Close(); } catch { }
                Log($"[FINS TCP] Client {ep} closed");
            }
        }

        private async Task<int> ReadExactAsync(Socket socket, byte[] buffer, int offset, int size, CancellationToken token)
        {
            int totalRead = 0;
            while (totalRead < size)
            {
                token.ThrowIfCancellationRequested();
                var seg = new ArraySegment<byte>(buffer, offset + totalRead, size - totalRead);
                int read = await Task.Factory.FromAsync(
                    socket.BeginReceive(seg.Array, seg.Offset, seg.Count, SocketFlags.None, null, null),
                    socket.EndReceive);

                if (read == 0) return totalRead;
                totalRead += read;
            }
            return totalRead;
        }
        #endregion

        #region UDP Handling
        private async Task ReceiveUdpPacketsAsync(UdpClient listener, CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    UdpReceiveResult result = await listener.ReceiveAsync();
                    byte[] requestData = result.Buffer;
                    string ep = result.RemoteEndPoint.ToString();

                    byte[] finsResponse = ProcessFinsFrame(requestData, ep, 10);
                    if (finsResponse != null)
                    {
                        await listener.SendAsync(finsResponse, finsResponse.Length, result.RemoteEndPoint);
                    }
                }
                catch (ObjectDisposedException) { break; }
                catch (Exception ex)
                {
                    if (token.IsCancellationRequested) break;
                    Log($"[FINS UDP] Error: {ex.Message}");
                }
            }
        }
        #endregion

        #region FINS Core Protocol Parsing
        /// <summary>
        /// Processes a raw 10-byte header FINS Frame and generates the FINS response frame.
        /// </summary>
        private byte[] ProcessFinsFrame(byte[] frame, string clientEp, byte assignedClientNode = 10)
        {
            if (frame == null || frame.Length < 12) return null;

            try
            {
                File.AppendAllText("fins_traffic.log", $"[{DateTime.Now:HH:mm:ss.fff}] RX ({frame.Length}B) from {clientEp}: {BitConverter.ToString(frame)}\r\n");
            }
            catch { }

            // FINS Header (10 bytes)
            byte icf = frame[0];
            byte rsv = frame[1];
            byte gct = frame[2];
            byte dna = frame[3]; // Dest network
            byte da1 = frame[4]; // Dest node
            byte da2 = frame[5]; // Dest unit
            byte sna = frame[6]; // Source network
            byte sa1 = frame[7]; // Source node
            byte sa2 = frame[8]; // Source unit
            byte sid = frame[9]; // Service ID

            // FINS Command Code (2 bytes)
            byte mr = frame[10]; // Main Request
            byte sr = frame[11]; // Sub Request

            // Response Header:
            // ICF: 0xC0 (Response required/generated)
            // Destination becomes request's Source
            // CRITICAL FIX: If sa1 is 0, destination node MUST be assignedClientNode!
            byte respIcf = 0xC0;
            byte respDna = sna;
            byte respDa1 = sa1 == 0 ? (assignedClientNode == 0 ? (byte)10 : assignedClientNode) : sa1;
            byte respDa2 = sa2;
            byte respSna = dna;
            byte respSa1 = da1 == 0 ? (byte)1 : da1;
            byte respSa2 = da2;

            byte[] header = new byte[10]
            {
                respIcf, rsv, gct, respDna, respDa1, respDa2, respSna, respSa1, respSa2, sid
            };

            // 1. Command 01 01: Memory Area Read
            if (mr == 0x01 && sr == 0x01)
            {
                if (frame.Length < 18) return CreateErrorResponse(header, mr, sr, 0x11, 0x01); // Param error

                byte areaCode = frame[12];
                int wordAddr = (frame[13] << 8) | frame[14];
                int bitAddr = frame[15];
                int count = (frame[16] << 8) | frame[17];

                if (!TryGetMemoryArea(areaCode, wordAddr, out OmronSimulatorEngine.MemoryArea area, out int actualWord, out bool isBit))
                {
                    Log($"[FINS READ] Unsupported area code 0x{areaCode:X2} from {clientEp}");
                    return CreateErrorResponse(header, mr, sr, 0x11, 0x04); // Address range error
                }

                if (isBit)
                {
                    // Read individual bits
                    byte[] bitData = new byte[count];
                    int currentWord = actualWord;
                    int currentBit = bitAddr;

                    for (int i = 0; i < count; i++)
                    {
                        bool? b;
                        if (area == OmronSimulatorEngine.MemoryArea.TIM_FLAG || area == OmronSimulatorEngine.MemoryArea.CNT_FLAG)
                        {
                            b = _simEngine.ReadBit(area, actualWord + i, 0);
                        }
                        else
                        {
                            b = _simEngine.ReadBit(area, currentWord, currentBit);
                            currentBit++;
                            if (currentBit >= 16)
                            {
                                currentBit = 0;
                                currentWord++;
                            }
                        }
                        bitData[i] = (b == true) ? (byte)1 : (byte)0;
                    }

                    string logAddr = (area == OmronSimulatorEngine.MemoryArea.TIM_FLAG || area == OmronSimulatorEngine.MemoryArea.CNT_FLAG)
                        ? $"{GetAreaName(area)}{actualWord}"
                        : $"{GetAreaName(area)}{actualWord}.{bitAddr:D2}";
                    Log($"[FINS READ BIT] {logAddr} (Count: {count}) => [{(bitData[0] == 1 ? "1" : "0")}] to {clientEp}");
                    return BuildSuccessResponse(header, mr, sr, bitData);
                }
                else
                {
                    // Read 16-bit words (each word is 2 bytes Big-Endian in FINS)
                    byte[] wordData = new byte[count * 2];
                    for (int i = 0; i < count; i++)
                    {
                        ushort? val = _simEngine.ReadWord(area, actualWord + i);
                        ushort w = val ?? 0;
                        // FINS is Big Endian (High byte, Low byte)
                        wordData[i * 2] = (byte)((w >> 8) & 0xFF);
                        wordData[i * 2 + 1] = (byte)(w & 0xFF);
                    }

                    ushort firstVal = count > 0 ? (ushort)((wordData[0] << 8) | wordData[1]) : (ushort)0;
                    Log($"[FINS READ WORD] {GetAreaName(area)}{actualWord} (Count: {count}) => 0x{firstVal:X4} ({firstVal}) to {clientEp}");
                    return BuildSuccessResponse(header, mr, sr, wordData);
                }
            }
            // 2. Command 01 02: Memory Area Write
            else if (mr == 0x01 && sr == 0x02)
            {
                if (frame.Length < 18) return CreateErrorResponse(header, mr, sr, 0x11, 0x01);

                byte areaCode = frame[12];
                int wordAddr = (frame[13] << 8) | frame[14];
                int bitAddr = frame[15];
                int count = (frame[16] << 8) | frame[17];

                if (!TryGetMemoryArea(areaCode, wordAddr, out OmronSimulatorEngine.MemoryArea area, out int actualWord, out bool isBit))
                {
                    Log($"[FINS WRITE] Unsupported area code 0x{areaCode:X2} from {clientEp}");
                    return CreateErrorResponse(header, mr, sr, 0x11, 0x04);
                }

                int dataOffset = 18;
                if (isBit)
                {
                    // Write individual bits (each bit sent as 1 byte in FINS)
                    int currentWord = actualWord;
                    int currentBit = bitAddr;

                    for (int i = 0; i < count && (dataOffset + i) < frame.Length; i++)
                    {
                        bool bitVal = frame[dataOffset + i] != 0;
                        if (area == OmronSimulatorEngine.MemoryArea.TIM_FLAG || area == OmronSimulatorEngine.MemoryArea.CNT_FLAG)
                        {
                            _simEngine.WriteBit(area, actualWord + i, 0, bitVal);
                        }
                        else
                        {
                            _simEngine.WriteBit(area, currentWord, currentBit, bitVal);
                            currentBit++;
                            if (currentBit >= 16)
                            {
                                currentBit = 0;
                                currentWord++;
                            }
                        }
                    }

                    bool firstBitVal = frame.Length > dataOffset && frame[dataOffset] != 0;
                    string logAddr = (area == OmronSimulatorEngine.MemoryArea.TIM_FLAG || area == OmronSimulatorEngine.MemoryArea.CNT_FLAG)
                        ? $"{GetAreaName(area)}{actualWord}"
                        : $"{GetAreaName(area)}{actualWord}.{bitAddr:D2}";
                    Log($"[FINS WRITE BIT] {logAddr} <= {(firstBitVal ? "1 (ON)" : "0 (OFF)")} from {clientEp}");
                    return BuildSuccessResponse(header, mr, sr, null);
                }
                else
                {
                    // Write 16-bit words (each word is 2 bytes Big-Endian)
                    for (int i = 0; i < count && (dataOffset + i * 2 + 1) < frame.Length; i++)
                    {
                        ushort wVal = (ushort)((frame[dataOffset + i * 2] << 8) | frame[dataOffset + i * 2 + 1]);
                        _simEngine.WriteWord(area, actualWord + i, wVal);
                    }

                    ushort firstVal = frame.Length >= dataOffset + 2 ? (ushort)((frame[dataOffset] << 8) | frame[dataOffset + 1]) : (ushort)0;
                    Log($"[FINS WRITE WORD] {GetAreaName(area)}{actualWord} <= 0x{firstVal:X4} ({firstVal}) from {clientEp}");
                    return BuildSuccessResponse(header, mr, sr, null);
                }
            }
            // 3. Command 01 03: Memory Area Fill (Used by SCADA/HMI to write registers)
            else if (mr == 0x01 && sr == 0x03)
            {
                if (frame.Length < 18) return CreateErrorResponse(header, mr, sr, 0x11, 0x01);

                byte areaCode = frame[12];
                int wordAddr = (frame[13] << 8) | frame[14];
                int bitAddr = frame[15];
                int count = (frame[16] << 8) | frame[17];

                if (!TryGetMemoryArea(areaCode, wordAddr, out OmronSimulatorEngine.MemoryArea area, out int actualWord, out bool isBit))
                {
                    Log($"[FINS FILL] Unsupported area code 0x{areaCode:X2} from {clientEp}");
                    return CreateErrorResponse(header, mr, sr, 0x11, 0x04);
                }

                int dataOffset = 18;
                if (isBit)
                {
                    bool bitVal = frame.Length > dataOffset && frame[dataOffset] != 0;
                    for (int i = 0; i < count; i++)
                    {
                        if (area == OmronSimulatorEngine.MemoryArea.TIM_FLAG || area == OmronSimulatorEngine.MemoryArea.CNT_FLAG)
                            _simEngine.WriteBit(area, actualWord + i, 0, bitVal);
                        else
                            _simEngine.WriteBit(area, actualWord, bitAddr + i, bitVal);
                    }
                    Log($"[FINS FILL BIT] {GetAreaName(area)}{actualWord} (Count: {count}) <= {(bitVal ? "1" : "0")} from {clientEp}");
                }
                else
                {
                    ushort wVal = frame.Length >= dataOffset + 2 ? (ushort)((frame[dataOffset] << 8) | frame[dataOffset + 1]) : (ushort)0;
                    for (int i = 0; i < count; i++)
                    {
                        _simEngine.WriteWord(area, actualWord + i, wVal);
                    }
                    Log($"[FINS FILL WORD] {GetAreaName(area)}{actualWord} (Count: {count}) <= 0x{wVal:X4} ({wVal}) from {clientEp}");
                }
                return BuildSuccessResponse(header, mr, sr, null);
            }
            // 4. Command 05 01: Read Controller Data (CPU Model / Unit Type)
            else if (mr == 0x05 && sr == 0x01)
            {
                // Returns 20 bytes controller model + 20 bytes version info
                byte[] cpuData = new byte[40];
                string activeModel = "CJ2M-CPU33";
                if (_simEngine != null && !string.IsNullOrWhiteSpace(_simEngine.DetectedCpuType))
                {
                    activeModel = _simEngine.DetectedCpuType;
                }

                string paddedModel = activeModel.PadRight(20).Substring(0, 20);
                byte[] modelBytes = Encoding.ASCII.GetBytes(paddedModel);
                byte[] verBytes = Encoding.ASCII.GetBytes("V2.00               ");
                Buffer.BlockCopy(modelBytes, 0, cpuData, 0, Math.Min(20, modelBytes.Length));
                Buffer.BlockCopy(verBytes, 0, cpuData, 20, Math.Min(20, verBytes.Length));

                Log($"[FINS INFO] CPU Model Info ({activeModel}) queried by {clientEp}");
                return BuildSuccessResponse(header, mr, sr, cpuData);
            }
            // 5. Default / Unsupported Command handler
            else
            {
                Log($"[FINS CMD] Code 0x{mr:X2} 0x{sr:X2} received from {clientEp}. Responding Success.");
                return BuildSuccessResponse(header, mr, sr, null);
            }
        }

        private byte[] BuildSuccessResponse(byte[] header, byte mr, byte sr, byte[] data)
        {
            int dataLen = data?.Length ?? 0;
            byte[] resp = new byte[10 + 2 + 2 + dataLen];

            // 1. Header (10 bytes)
            Buffer.BlockCopy(header, 0, resp, 0, 10);

            // 2. Command Code (2 bytes)
            resp[10] = mr;
            resp[11] = sr;

            // 3. End Code (2 bytes): 0x00 0x00 = Normal Completion
            resp[12] = 0x00;
            resp[13] = 0x00;

            // 4. Data payload
            if (dataLen > 0)
            {
                Buffer.BlockCopy(data, 0, resp, 14, dataLen);
            }

            try
            {
                File.AppendAllText("fins_traffic.log", $"[{DateTime.Now:HH:mm:ss.fff}] TX OK ({resp.Length}B): {BitConverter.ToString(resp)}\r\n");
            }
            catch { }

            return resp;
        }

        private byte[] CreateErrorResponse(byte[] header, byte mr, byte sr, byte mres, byte sres)
        {
            byte[] resp = new byte[14];
            Buffer.BlockCopy(header, 0, resp, 0, 10);
            resp[10] = mr;
            resp[11] = sr;
            resp[12] = mres;
            resp[13] = sres;

            try
            {
                File.AppendAllText("fins_traffic.log", $"[{DateTime.Now:HH:mm:ss.fff}] TX ERR ({resp.Length}B, 0x{mres:X2}{sres:X2}): {BitConverter.ToString(resp)}\r\n");
            }
            catch { }

            return resp;
        }

        private bool TryGetMemoryArea(byte code, int wordAddr, out OmronSimulatorEngine.MemoryArea area, out int actualWord, out bool isBit)
        {
            area = OmronSimulatorEngine.MemoryArea.CIO;
            actualWord = wordAddr;
            isBit = false;

            switch (code)
            {
                // CIO Area
                case 0x30:
                    area = OmronSimulatorEngine.MemoryArea.CIO;
                    isBit = true;
                    return true;
                case 0xB0:
                    area = OmronSimulatorEngine.MemoryArea.CIO;
                    isBit = false;
                    return true;

                // Work Area (WR / W)
                case 0x31:
                    area = OmronSimulatorEngine.MemoryArea.WR;
                    isBit = true;
                    return true;
                case 0xB1:
                    area = OmronSimulatorEngine.MemoryArea.WR;
                    isBit = false;
                    return true;

                // Holding Area (HR / H)
                case 0x32:
                    area = OmronSimulatorEngine.MemoryArea.HR;
                    isBit = true;
                    return true;
                case 0xB2:
                    area = OmronSimulatorEngine.MemoryArea.HR;
                    isBit = false;
                    return true;

                // Auxiliary Area (AR / A)
                case 0x33:
                    area = OmronSimulatorEngine.MemoryArea.AR;
                    isBit = true;
                    return true;
                case 0xB3:
                    area = OmronSimulatorEngine.MemoryArea.AR;
                    isBit = false;
                    return true;

                // Data Memory Area (DM / D)
                case 0x02:
                    area = OmronSimulatorEngine.MemoryArea.DM;
                    isBit = true;
                    return true;
                case 0x82:
                case 0x80: // CV-series DM Word
                    area = OmronSimulatorEngine.MemoryArea.DM;
                    isBit = false;
                    return true;

                // Data Register (DR0..DR15) in Omron CS/CJ/CP
                case 0xBC:
                    area = OmronSimulatorEngine.MemoryArea.DM;
                    actualWord = wordAddr;
                    isBit = false;
                    return true;

                // Index Register (IR0..IR15)
                case 0xDC:
                    area = OmronSimulatorEngine.MemoryArea.DM;
                    actualWord = wordAddr;
                    isBit = false;
                    return true;

                // Timer / Counter Completion Flag (Bit)
                // In standard FINS: 0x09 is TIM/CNT Flag. 
                // 0000 - 0FFF = Timer Flag, 8000 - 8FFF = Counter Flag.
                // 0x01 is Timer Flag in CV/CS/CJ, 0x0A or 0x08 is Counter Flag.
                case 0x09:
                case 0x01:
                    if (wordAddr >= 0x8000)
                    {
                        area = OmronSimulatorEngine.MemoryArea.CNT_FLAG;
                        actualWord = wordAddr - 0x8000;
                    }
                    else
                    {
                        area = OmronSimulatorEngine.MemoryArea.TIM_FLAG;
                        actualWord = wordAddr;
                    }
                    isBit = true;
                    return true;

                case 0x0A:
                case 0x08:
                    area = OmronSimulatorEngine.MemoryArea.CNT_FLAG;
                    actualWord = wordAddr >= 0x8000 ? wordAddr - 0x8000 : wordAddr;
                    isBit = true;
                    return true;

                // Timer / Counter Present Value (PV / Word - timer & counter berjalan)
                // In standard FINS: 0x89 is TIM/CNT PV.
                // 0000 - 0FFF = Timer PV, 8000 - 8FFF = Counter PV.
                // 0x81 is Timer PV in CV/CS/CJ, 0x8A or 0x88 is Counter PV.
                case 0x89:
                case 0x81:
                    if (wordAddr >= 0x8000)
                    {
                        area = OmronSimulatorEngine.MemoryArea.CNT_PV;
                        actualWord = wordAddr - 0x8000;
                    }
                    else
                    {
                        area = OmronSimulatorEngine.MemoryArea.TIM_PV;
                        actualWord = wordAddr;
                    }
                    isBit = false;
                    return true;

                case 0x8A:
                case 0x88:
                    area = OmronSimulatorEngine.MemoryArea.CNT_PV;
                    actualWord = wordAddr >= 0x8000 ? wordAddr - 0x8000 : wordAddr;
                    isBit = false;
                    return true;

                // Extended Memory (EM / E)
                case 0x20:
                case 0x21:
                    area = OmronSimulatorEngine.MemoryArea.EM;
                    isBit = true;
                    return true;
                case 0xA0:
                case 0xA1:
                    area = OmronSimulatorEngine.MemoryArea.EM;
                    isBit = false;
                    return true;

                default:
                    return false;
            }
        }

        private string GetAreaName(OmronSimulatorEngine.MemoryArea area)
        {
            switch (area)
            {
                case OmronSimulatorEngine.MemoryArea.WR: return "W";
                case OmronSimulatorEngine.MemoryArea.HR: return "H";
                case OmronSimulatorEngine.MemoryArea.AR: return "A";
                case OmronSimulatorEngine.MemoryArea.DM: return "D";
                case OmronSimulatorEngine.MemoryArea.EM: return "E";
                case OmronSimulatorEngine.MemoryArea.TIM_PV: return "T(PV)";
                case OmronSimulatorEngine.MemoryArea.CNT_PV: return "C(PV)";
                case OmronSimulatorEngine.MemoryArea.TIM_FLAG: return "T(Flag)";
                case OmronSimulatorEngine.MemoryArea.CNT_FLAG: return "C(Flag)";
                default: return "CIO";
            }
        }

        private static void SetBigEndian32(byte[] buffer, int offset, int value)
        {
            buffer[offset] = (byte)((value >> 24) & 0xFF);
            buffer[offset + 1] = (byte)((value >> 16) & 0xFF);
            buffer[offset + 2] = (byte)((value >> 8) & 0xFF);
            buffer[offset + 3] = (byte)(value & 0xFF);
        }
        #endregion

        private void Log(string msg)
        {
            OnLog?.Invoke(msg);
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
