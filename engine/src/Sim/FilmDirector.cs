using System;
using System.Collections.Generic;
using System.Globalization;
using Godot;
using FactoryForge.Editor;
using FactoryForge.TagBus;
using FactoryForge.View;

namespace FactoryForge.Sim;

/// <summary>
/// The command line's stage manager, for filming a scene (tools/record_demo.py).
///
/// Everything here is opt-in. <c>Main</c> only adds one when <c>--film</c>,
/// <c>--camera=</c> (and <c>--camera-to=</c>), <c>--at=</c> or <c>--watch=</c> is on the command line, so
/// a run without those flags is exactly the run it always was.
///
/// <b>The clock is the game clock.</b> Time here is physics ticks divided by
/// the tick rate, counted from the first tick after the scene loaded, and not
/// the wall clock. Godot's Movie Maker writes one frame per fixed step of that
/// same clock however slowly it encodes, so an action scheduled at 6.5 s lands
/// on the frame at 6.5 s and a log line stamped 6.5 s can be cut around.
///
/// <b>Actions go through the same doors a person would use.</b>
/// <list type="bullet">
/// <item><c>force:TAG=VALUE</c> and <c>release:TAG</c> are the Tag Inspector's
/// Force and Unforce buttons (<see cref="TagTable.Force"/>).</item>
/// <item><c>operate:PART</c> is a click on the part in Run mode: it calls
/// <c>SceneEditor.OperatePartById</c>, which is the call a click ends in, so
/// a guard door slides, or refuses while its solenoid holds it, exactly as it
/// would for a mouse. Forcing the door's <c>closed</c> tag instead would skip
/// the door and lie to the relay about a door that had not moved.</item>
/// </list>
///
/// Every action, and every change of a watched tag, is printed with its game
/// time as <c>[film] t=6.500 ...</c>.
/// </summary>
public partial class FilmDirector : Node
{
    public TagTable Tags { get; set; } = null!;
    public SceneEditor? Editor { get; set; }

    /// <summary>Hide every panel, toolbar and overlay so the 3D view fills the
    /// frame. Nothing in the simulation reads a panel's visibility.</summary>
    public bool Film { get; set; }

    /// <summary>Where the panels live: the children of this node that are
    /// <see cref="Control"/>s are hidden. Defaults to this node's parent.</summary>
    public Node? UiRoot { get; set; }

    /// <summary>yaw and pitch in degrees, distance and look-at target in metres.
    /// See <see cref="OrbitCamera.Place"/>.</summary>
    public (float Yaw, float Pitch, float Distance, Vector3 Target)? CameraPose { get; set; }

    /// <summary>Actions that could not be carried out (unknown tag or part).</summary>
    public int Failures { get; private set; }

    /// <summary>Game time of the tick that carried out each action, in the
    /// order they were scheduled. The self-test reads it back.</summary>
    public IReadOnlyList<double> FiredAt => _firedAt;

    /// <summary>Every logged watched-tag change, as (time, tag, value).</summary>
    public IReadOnlyList<(double Time, string Tag, string Value)> Changes => _changes;

    private sealed class Step
    {
        public double At;
        public string Verb = "";
        public string Arg = "";
        public string Text = "";
        public bool Done;
    }

    private readonly List<Step> _steps = new();
    private readonly List<double> _firedAt = new();
    private readonly List<string> _watch = new();
    private readonly Dictionary<string, string> _last = new();
    private readonly List<(double, string, string)> _changes = new();
    private long _ticks;
    private bool _posed;

    /// <summary>Game time in seconds since the scene loaded.</summary>
    public double GameTime => _ticks / (double)Engine.PhysicsTicksPerSecond;

    /// <summary>Parse one <c>--at=</c> value, <c>SECONDS:ACTION</c>. Returns an
    /// error message, or null when it was accepted.</summary>
    public string? Schedule(string spec)
    {
        int colon = spec.IndexOf(':');
        if (colon <= 0) return $"--at={spec}: expected SECONDS:ACTION";
        if (!double.TryParse(spec.AsSpan(0, colon), NumberStyles.Float, CultureInfo.InvariantCulture, out double at)
            || at < 0)
            return $"--at={spec}: '{spec[..colon]}' is not a time in seconds";

        string action = spec[(colon + 1)..];
        int c2 = action.IndexOf(':');
        string verb = c2 < 0 ? action : action[..c2];
        string arg = c2 < 0 ? "" : action[(c2 + 1)..];
        if (verb is not ("force" or "release" or "operate"))
            return $"--at={spec}: unknown action '{verb}' (force, release, operate)";
        if (arg.Length == 0) return $"--at={spec}: '{verb}' needs an argument";
        if (verb == "force" && !arg.Contains('='))
            return $"--at={spec}: force wants TAG=VALUE";

        _steps.Add(new Step { At = at, Verb = verb, Arg = arg, Text = action });
        return null;
    }

    public void Watch(string csv)
    {
        foreach (var t in csv.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            if (!_watch.Contains(t)) _watch.Add(t);
    }

    /// <summary>Parse <c>--camera=yaw,pitch,distance:tx,ty,tz</c>. Returns an
    /// error message, or null.</summary>
    public string? SetCamera(string spec)
    {
        var (pose, problem) = ParsePose(spec, "--camera");
        if (problem is not null) return problem;
        CameraPose = pose;
        return null;
    }

    /// <summary>The pose <c>--camera-to=</c> moves to, and the game-time window
    /// it moves in.</summary>
    public ((float Yaw, float Pitch, float Distance, Vector3 Target) To, double Start, double End)? CameraMove { get; private set; }

    /// <summary>
    /// Parse <c>--camera-to=yaw,pitch,distance:tx,ty,tz@START-END</c>: glide
    /// from the <c>--camera=</c> pose to this one between two game times, eased
    /// in and out. A trailer opening on the real engine needs a camera that
    /// moves, and a still pose cut by a digital zoom looks like exactly that.
    /// Needs <c>--camera=</c>, which is where it starts. Returns an error
    /// message, or null.
    /// </summary>
    public string? SetCameraMove(string spec)
    {
        int at = spec.LastIndexOf('@');
        if (at < 0) return $"--camera-to={spec}: expected POSE@START-END";
        var (pose, problem) = ParsePose(spec[..at], "--camera-to");
        if (problem is not null) return problem;
        var window = spec[(at + 1)..].Split('-');
        if (window.Length != 2
            || !double.TryParse(window[0], NumberStyles.Float, CultureInfo.InvariantCulture, out double start)
            || !double.TryParse(window[1], NumberStyles.Float, CultureInfo.InvariantCulture, out double end)
            || start < 0 || end <= start)
            return $"--camera-to={spec}: expected @START-END in seconds, END after START";
        if (CameraPose is null) return $"--camera-to={spec}: needs --camera= to say where it starts";
        CameraMove = (pose, start, end);
        return null;
    }

    /// <summary>Where the camera is at game time <paramref name="t"/>: the
    /// <c>--camera=</c> pose before the move, the <c>--camera-to=</c> pose
    /// after it, and a smoothstep between. Pure, so the headless self-test can
    /// check the path without a camera.</summary>
    public (float Yaw, float Pitch, float Distance, Vector3 Target)? PoseAt(double t)
    {
        if (CameraPose is not { } from) return null;
        if (CameraMove is not { } move) return from;
        float k = (float)Math.Clamp((t - move.Start) / (move.End - move.Start), 0, 1);
        k = k * k * (3 - 2 * k);
        var to = move.To;
        return (Mathf.Lerp(from.Yaw, to.Yaw, k), Mathf.Lerp(from.Pitch, to.Pitch, k),
                Mathf.Lerp(from.Distance, to.Distance, k), from.Target.Lerp(to.Target, k));
    }

    private static ((float, float, float, Vector3) Pose, string? Problem) ParsePose(string spec, string flag)
    {
        var halves = spec.Split(':');
        if (halves.Length != 2) return (default, $"{flag}={spec}: expected yaw,pitch,distance:tx,ty,tz");
        var a = ParseFloats(halves[0]);
        var b = ParseFloats(halves[1]);
        if (a is not { Length: 3 } || b is not { Length: 3 })
            return (default, $"{flag}={spec}: expected three numbers on each side of the colon");
        return ((a[0], a[1], a[2], new Vector3(b[0], b[1], b[2])), null);
    }

    private static float[]? ParseFloats(string csv)
    {
        var parts = csv.Split(',');
        var r = new float[parts.Length];
        for (int i = 0; i < parts.Length; i++)
            if (!float.TryParse(parts[i], NumberStyles.Float, CultureInfo.InvariantCulture, out r[i]))
                return null;
        return r;
    }

    public override void _Ready()
    {
        UiRoot ??= GetParent();
        ApplyView();
    }

    public override void _Process(double delta)
    {
        // Re-hidden every frame: several panels show themselves again on their
        // own (the mode hint, the idle hint, a confirm dialog), and a film
        // frame with one of them in it is a film frame nobody can use.
        if (Film) HidePanels();
    }

    public override void _PhysicsProcess(double delta)
    {
        if (Tags is null) return;
        if (!_posed) { ApplyView(); _posed = true; }

        double t = GameTime;
        if (CameraMove is not null) MoveCamera(t);
        // Half a tick of slack so an action at 2.0 s fires on the tick that
        // reads 2.000 rather than one later to floating point.
        double half = 0.5 / Engine.PhysicsTicksPerSecond;
        for (int i = 0; i < _steps.Count; i++)
        {
            var s = _steps[i];
            if (s.Done || t + half < s.At) continue;
            s.Done = true;
            Perform(s, t);
        }

        foreach (var tag in _watch)
        {
            if (!Tags.TryGetVisible(tag, out var raw)) continue;
            string text = Format(raw);
            if (_last.TryGetValue(tag, out var before) && before == text) continue;
            _last[tag] = text;
            _changes.Add((t, tag, text));
            GD.Print($"[film] t={t.ToString("F3", CultureInfo.InvariantCulture)} {tag}={text}");
        }
        _ticks++;
    }

    private static string Format(object v) => v switch
    {
        bool b => b ? "true" : "false",
        double d => d.ToString("0.###", CultureInfo.InvariantCulture),
        float f => f.ToString("0.###", CultureInfo.InvariantCulture),
        _ => Convert.ToString(v, CultureInfo.InvariantCulture) ?? "",
    };

    private void Perform(Step s, double t)
    {
        _firedAt.Add(t);
        string stamp = $"[film] t={t.ToString("F3", CultureInfo.InvariantCulture)}";
        try
        {
            switch (s.Verb)
            {
                case "force":
                {
                    int eq = s.Arg.IndexOf('=');
                    string id = s.Arg[..eq], text = s.Arg[(eq + 1)..];
                    if (!Tags.Contains(id)) { Fail($"{stamp} action {s.Text}: no such tag"); return; }
                    Tags.Force(id, ParseValue(Tags[id].Type, text));
                    break;
                }
                case "release":
                    if (!Tags.Contains(s.Arg)) { Fail($"{stamp} action {s.Text}: no such tag"); return; }
                    Tags.ClearForce(s.Arg);
                    break;
                case "operate":
                    if (Editor is null || !Editor.OperatePartById(s.Arg))
                    {
                        Fail($"{stamp} action {s.Text}: no such part");
                        return;
                    }
                    break;
            }
        }
        catch (Exception ex) when (ex is ArgumentException or FormatException)
        {
            Fail($"{stamp} action {s.Text}: {ex.Message}");
            return;
        }
        GD.Print($"{stamp} action {s.Text}");
    }

    private void Fail(string message)
    {
        Failures++;
        GD.PrintErr(message);
    }

    private static object ParseValue(TagType type, string text) => type switch
    {
        TagType.Bit => text.ToLowerInvariant() switch
        {
            "true" or "1" => true,
            "false" or "0" => false,
            _ => throw new FormatException($"'{text}' is not true/false"),
        },
        TagType.Int => int.Parse(text, CultureInfo.InvariantCulture),
        _ => double.Parse(text, CultureInfo.InvariantCulture),
    };

    /// <summary>The camera pose, and the panels. Both are display-only: a
    /// headless run has neither camera nor panels and this does nothing.</summary>
    private void ApplyView()
    {
        if (DisplayServer.GetName() == "headless") return;
        if (CameraPose is { } p && UiRoot?.GetNodeOrNull<OrbitCamera>("OrbitCamera") is { } cam)
        {
            cam.Place(p.Yaw, p.Pitch, p.Distance, p.Target);
            if (!cam.Current) cam.MakeCurrent();
        }
        if (Film) HidePanels();
    }

    /// <summary>One step of <c>--camera-to=</c>, on the physics tick so the
    /// move is on the game clock that Movie Maker's frames are on.</summary>
    private void MoveCamera(double t)
    {
        if (DisplayServer.GetName() == "headless") return;
        if (PoseAt(t) is { } p && UiRoot?.GetNodeOrNull<OrbitCamera>("OrbitCamera") is { } cam)
            cam.Place(p.Yaw, p.Pitch, p.Distance, p.Target);
    }

    /// <summary>Hide every panel. All of them are <see cref="Control"/>s
    /// parented straight to the root node (Main.BuildView), so this needs no
    /// list that could go stale when a panel is added.</summary>
    public int HidePanels()
    {
        int hidden = 0;
        if (UiRoot is null) return 0;
        foreach (var child in UiRoot.GetChildren())
        {
            if (child is Control { Visible: true } control) { control.Visible = false; hidden++; }
        }
        return hidden;
    }
}
