using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Nodes;
using Godot;

namespace FactoryForge.TagBus;

/// <summary>
/// Engine-side tag bus. Speaks the protocol in docs/tag-bus.md, so the
/// unchanged Python sidecar and all its drivers connect to this exactly as they
/// do to harness/engine_stub.py.
///
/// Only one sidecar may connect at a time: two drivers writing the same output
/// tag is a bug, not a feature.
/// </summary>
public partial class TagBusServer : Node
{
    public const int ProtocolVersion = 0;
    public const string EngineId = "factoryforge-engine/0.1.0";

    [Export] public int Port { get; set; } = 7411;
    [Export] public int TickMs { get; set; } = 10;

    private TcpServer _listener = new();
    private WebSocketPeer? _client;
    private readonly Dictionary<string, object> _lastSent = new();
    private readonly Dictionary<string, object> _lastForced = new();

    /// <summary>Sidecars being turned away, each held only long enough to
    /// finish its handshake, hear why, and be closed. See
    /// <see cref="PumpRefusals"/>.</summary>
    private readonly List<(WebSocketPeer Peer, bool Told, ulong Deadline)> _refusing = new();

    /// <summary>How long a refused peer is given to finish its handshake before
    /// it is dropped without ceremony. It only has to say hello over loopback;
    /// anything slower than this is not a sidecar.</summary>
    private const ulong RefusalTimeoutMs = 5000;

    public TagTable Tags { get; set; } = new();
    public string SceneName { get; set; } = "untitled";
    /// <summary>Did the socket actually bind? False means no driver can ever
    /// connect to this instance, however healthy the rest of it looks.</summary>
    public bool IsListening { get; private set; }

    public int Epoch { get; private set; }
    public long TickCount { get; private set; }
    public bool HasClient => _client is not null &&
                             _client.GetReadyState() == WebSocketPeer.State.Open;

    public override void _Ready()
    {
        // Retry for a few seconds. Closing the app and reopening it is an
        // ordinary thing to do, and the previous instance's socket is not
        // always released by the time the new one asks for it — a single short
        // retry left the restarted engine silently unreachable.
        var err = _listener.Listen((ushort)Port, "127.0.0.1");
        for (int attempt = 0; err != Error.Ok && attempt < 6; attempt++)
        {
            System.Threading.Thread.Sleep(500);
            err = _listener.Listen((ushort)Port, "127.0.0.1");
        }

        IsListening = err == Error.Ok;
        if (!IsListening)
        {
            // Almost always a second engine already running. Worth saying so:
            // the scene still renders and still simulates, so without this the
            // window looks perfectly healthy and no controller can ever reach
            // it — which reads as "the PLC connection is broken".
            GD.PushError($"tag bus could not listen on port {Port}: {err}. " +
                         "Another FactoryForge engine is probably already running — " +
                         "close it, or this instance will render but no driver can connect.");
        }
        else
        {
            GD.Print($"tag bus listening on ws://127.0.0.1:{Port}/tagbus");
        }
    }

    public override void _Process(double delta)
    {
        AcceptPending();
        PumpClient();
    }

    private void AcceptPending()
    {
        PumpRefusals();
        if (!_listener.IsConnectionAvailable()) return;
        var stream = _listener.TakeConnection();
        if (stream is null) return;

        // The current client's state is only ever as fresh as the last Poll().
        // A sidecar that vanished *without* a close frame — a crash, a killed
        // process, a pulled cable — still reads as Open until it is polled, and
        // this runs before PumpClient does that. So poll here, before judging a
        // new connection: without it the first reconnect after a crash is
        // refused as a "second sidecar" while the first one is already gone.
        // Measured against this engine before the fix: 15 attempts, 15 refusals,
        // every one of them recovering only on the following frame. Python's
        // engine_stub answered the same reconnect immediately, which makes it a
        // parity divergence and not only a robustness bug (HP-19).
        if (_client is not null)
        {
            _client.Poll();
            if (_client.GetReadyState() == WebSocketPeer.State.Closed)
            {
                GD.Print("tag bus: sidecar disconnected");
                DropClient();
            }
        }

        var peer = new WebSocketPeer();
        if (peer.AcceptStream(stream) != Error.Ok)
        {
            GD.PushWarning("tag bus: websocket handshake failed");
            return;
        }

        if (_client is not null)
        {
            // Refuse rather than silently multiplexing — but say why, on the
            // bus. This used to be `stream.DisconnectFromHost()`, which tore
            // the TCP connection down before the websocket handshake finished,
            // so the second sidecar saw "did not receive a valid HTTP
            // response" and never learned that another sidecar was the reason.
            // engine_stub.py has always completed the handshake and answered
            // with a status; the two engines have to answer this the same way.
            _refusing.Add((peer, false, Time.GetTicksMsec() + RefusalTimeoutMs));
            GD.Print("tag bus: refused a second sidecar");
            return;
        }
        _client = peer;
    }

    /// <summary>Carry each refused sidecar as far as being told why.
    ///
    /// A peer cannot be sent anything until its handshake completes, and that
    /// takes a poll or two, so the refusal cannot be done inline in
    /// <see cref="AcceptPending"/> — it is a tiny state machine of its own.
    /// </summary>
    private void PumpRefusals()
    {
        for (int i = _refusing.Count - 1; i >= 0; i--)
        {
            var (peer, told, deadline) = _refusing[i];
            peer.Poll();
            var state = peer.GetReadyState();
            if (state == WebSocketPeer.State.Closed || Time.GetTicksMsec() > deadline)
            {
                if (state != WebSocketPeer.State.Closed) peer.Close();
                _refusing.RemoveAt(i);
                continue;
            }
            if (state != WebSocketPeer.State.Open || told) continue;

            peer.SendText(new JsonObject
            {
                ["t"] = "status",
                ["level"] = "error",
                ["code"] = "already_connected",
                ["message"] = "another sidecar is already connected",
            }.ToJsonString());
            peer.Close(1000, "another sidecar is already connected");
            _refusing[i] = (peer, true, deadline);
        }
    }

    /// <summary>Forget the current client and everything derived from it. The
    /// delta baselines belong to one session: kept across a reconnect they
    /// would suppress the first change of the next one.</summary>
    private void DropClient()
    {
        _client = null;
        _greeted = false;
        _lastSent.Clear();
        _lastForced.Clear();
    }

    private void PumpClient()
    {
        if (_client is null) return;
        _client.Poll();

        switch (_client.GetReadyState())
        {
            case WebSocketPeer.State.Open:
                break;
            case WebSocketPeer.State.Closed:
                GD.Print("tag bus: sidecar disconnected");
                DropClient();
                return;
            default:
                return; // still connecting or closing
        }

        if (!_greeted)
        {
            _greeted = true;
            Send(Hello());
            SendDescribe();
        }

        while (_client.GetAvailablePacketCount() > 0)
        {
            var text = _client.GetPacket().GetStringFromUtf8();
            try
            {
                var msg = JsonNode.Parse(text)?.AsObject();
                if (msg is not null) Handle(msg);
            }
            catch (Exception e)
            {
                // Say so on the bus, not only in the engine's log. A frame the
                // engine cannot read is the sidecar's problem to fix, and a
                // sidecar that is never told simply believes its write landed.
                // The Python engine answers the same frame the same way, which
                // is the whole point of HP-19.
                GD.PushWarning($"tag bus: bad message: {e.Message}");
                Status("warn", "bad_message", $"could not read a frame: {e.Message}");
            }
        }
    }

    private bool _greeted;

    // --- outgoing ---

    private JsonObject Hello() => new()
    {
        ["t"] = "hello",
        ["protocol"] = ProtocolVersion,
        ["engine"] = EngineId,
        ["tick_ms"] = TickMs,
    };

    /// <summary>Publish the current tag set and bump the epoch.</summary>
    public void SendDescribe()
    {
        Epoch++;
        _lastSent.Clear();
        foreach (var tag in Tags) _lastSent[tag.Id] = Tags.Visible(tag.Id);

        // `describe` already carries every force, so the observe channel starts
        // from that baseline rather than repeating it on the next tick.
        _lastForced.Clear();
        foreach (var tag in Tags)
            if (Tags.IsForced(tag.Id)) _lastForced[tag.Id] = Tags.Visible(tag.Id);

        Send(new JsonObject
        {
            ["t"] = "describe",
            ["scene"] = SceneName,
            ["epoch"] = Epoch,
            ["tags"] = Tags.ToJson(),
        });
    }

    /// <summary>Send only the input tags whose values changed. Nothing is sent
    /// when nothing changed — an idle scene produces zero traffic.</summary>
    public void SendUpdates()
    {
        if (!HasClient) return;
        // Before the update, not after: a release has to reach the sidecar
        // ahead of the value it reveals, or the sidecar absorbs that value
        // underneath a pin it is about to drop.
        SendObservations();
        JsonObject? changed = null;
        foreach (var tag in Tags.ByKind(TagKind.Input))
        {
            var current = Tags.Visible(tag.Id);
            if (_lastSent.TryGetValue(tag.Id, out var prev) && Equals(prev, current))
                continue;
            _lastSent[tag.Id] = current;
            changed ??= new JsonObject();
            changed[tag.Id] = JsonValue.Create(current);
        }
        if (changed is null) return;

        Send(new JsonObject
        {
            ["t"] = "update",
            ["tick"] = TickCount,
            ["values"] = changed,
        });
    }

    /// <summary>Publish changes to the forced state, delta-only (HP-13).
    ///
    /// A separate message from `update` on purpose. `update` is simulator-input
    /// delivery and every driver's push() hook hangs off it; a driver that
    /// writes whatever it is handed would push a simulator-forced output
    /// straight back into the PLC node that owns it, which is a worse bug than
    /// the one this closes. Mirrors EngineStub._send_observations.</summary>
    private void SendObservations()
    {
        JsonObject? forced = null;
        JsonArray? cleared = null;
        var current = new Dictionary<string, object>();

        foreach (var tag in Tags)
        {
            if (!Tags.IsForced(tag.Id)) continue;
            var visible = Tags.Visible(tag.Id);
            current[tag.Id] = visible;
            if (_lastForced.TryGetValue(tag.Id, out var prev) && Equals(prev, visible))
                continue;
            (forced ??= new JsonObject())[tag.Id] = JsonValue.Create(visible);
        }
        foreach (var id in _lastForced.Keys)
            if (!current.ContainsKey(id)) (cleared ??= new JsonArray()).Add(id);

        if (forced is null && cleared is null) return;

        _lastForced.Clear();
        foreach (var entry in current) _lastForced[entry.Key] = entry.Value;

        Send(new JsonObject
        {
            ["t"] = "observe",
            ["tick"] = TickCount,
            ["forced"] = forced ?? new JsonObject(),
            ["cleared"] = cleared ?? new JsonArray(),
        });
    }

    public void Status(string level, string code, string message) => Send(new JsonObject
    {
        ["t"] = "status",
        ["level"] = level,
        ["code"] = code,
        ["message"] = message,
    });

    private void Send(JsonObject msg)
    {
        if (_client is null || _client.GetReadyState() != WebSocketPeer.State.Open) return;
        _client.SendText(msg.ToJsonString());
    }

    // --- incoming ---

    private void Handle(JsonObject msg)
    {
        switch (msg["t"]?.GetValue<string>())
        {
            case "write":
                // A write in flight across a scene change would otherwise land
                // on whatever tag inherited that id.
                if (!TryEpoch(msg, out var writeEpoch)) return;
                if (writeEpoch != Epoch) return;
                ApplyWrites(msg["values"]?.AsObject());
                break;

            case "force":
                if (!TryEpoch(msg, out var forceEpoch)) return;
                if (forceEpoch != Epoch) return;
                List<string>? badForces = null;
                if (msg["values"]?.AsObject() is { } forces)
                    foreach (var (id, node) in forces)
                    {
                        if (!Tags.Contains(id) || node is null) continue;
                        // Per value, exactly as ApplyWrites is: `force` runs
                        // the same coercion, and one bad value used to abort
                        // every release in the same message.
                        try { Tags.Force(id, ToClr(node)); }
                        catch (ArgumentException e) { (badForces ??= new()).Add($"{id} ({e.Message})"); }
                    }
                if (msg["clear"]?.AsArray() is { } clears)
                    foreach (var node in clears)
                        if (node is not null && Tags.Contains(node.GetValue<string>()))
                            Tags.ClearForce(node.GetValue<string>());
                if (badForces is not null)
                    Status("warn", "bad_value", $"rejected bad values: {string.Join("; ", badForces)}");
                break;

            case "status":
                GD.Print($"sidecar: {msg["message"]}");
                break;
        }
    }

    private void ApplyWrites(JsonObject? values)
    {
        if (values is null) return;
        List<string>? unknown = null;
        List<string>? rejected = null;
        foreach (var (id, node) in values)
        {
            var tag = Tags.Get(id);
            if (tag is null) { (unknown ??= new()).Add(id); continue; }
            if (tag.Kind != TagKind.Output)
            {
                Status("warn", "wrong_kind",
                    $"{id} is a simulator-owned input; use force to override it");
                continue;
            }
            if (node is null) continue;

            // Per value, not per message: one bad value used to abort every
            // other value in the same batch, silently and without naming the
            // offender. See FF-11.
            try
            {
                Tags.Set(id, ToClr(node));
            }
            catch (ArgumentException e)
            {
                (rejected ??= new()).Add($"{id} ({e.Message})");
            }
        }
        if (unknown is not null)
            Status("warn", "unknown_tags", $"ignored unknown tags: {string.Join(", ", unknown)}");
        if (rejected is not null)
            Status("warn", "bad_value", $"rejected bad values: {string.Join("; ", rejected)}");
    }

    /// <summary>The frame's <c>epoch</c>, which must be an integer.
    ///
    /// Says so on the bus when it is not, rather than letting the frame decide
    /// its own fate by accident. <c>GetValue&lt;int&gt;()</c> threw on anything
    /// that was not one, so a string epoch escaped into PumpClient's catch-all
    /// and came back as a bad_message with a .NET type name in it — while the
    /// Python engine dropped the same frame in silence and, for a fractional
    /// epoch like <c>3.0</c>, *applied* it, because Python compares 3.0 == 3.
    /// One frame, three different outcomes across two engines (HP-19).
    /// A missing or malformed epoch is now bad_message on both; an epoch that
    /// is an integer but not the current one is still dropped silently, which
    /// is what docs/tag-bus.md has always said a stale write gets.</summary>
    private bool TryEpoch(JsonObject msg, out int epoch)
    {
        epoch = 0;
        if (msg["epoch"] is JsonValue value && value.TryGetValue<int>(out epoch))
            return true;
        Status("warn", "bad_message",
            $"{msg["t"]}: epoch must be an integer, got {msg["epoch"]?.ToJsonString() ?? "nothing"}");
        return false;
    }

    private static object ToClr(JsonNode node)
    {
        var v = node.AsValue();
        if (v.TryGetValue<bool>(out var b)) return b;
        if (v.TryGetValue<int>(out var i)) return i;
        if (v.TryGetValue<double>(out var d)) return d;
        return v.ToString();
    }

    public void CountTick() => TickCount++;
}
