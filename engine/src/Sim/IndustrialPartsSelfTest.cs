using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check that the five industrial parts of IP-17 behave like the
/// devices they are named after, in the ways a PLC program can observe:
///
/// <code>godot --headless --path engine -- --self-test=industrialparts</code>
///
/// Every assertion is about an <em>effect</em> (gotcha 16):
/// <list type="bullet">
/// <item>A limit switch changes over only when something physically reaches
///   its lever — a carton on a running belt, or a cylinder's face plate at the
///   end of its stroke — and not for a carton that passes out of reach or
///   under a lever set too high. NO and NC are never made together, the
///   breaking contact never chatters, and a bouncing contact gives a PLC more
///   than one rising edge for one carton.</item>
/// <item>A solenoid valve's feedbacks are both false mid-stroke, arrive only
///   after the travel time, and never follow the command once it is stuck —
///   stuck shut, or stuck half way.</item>
/// <item>A star-delta starter's auxiliary contacts close a pull-in time after
///   the coils; it runs up in star at a third of the direct-on-line current; a
///   changeover that waits for the star contact to open is clean; star and
///   delta together — even with the main contactor out, and even when the two
///   coils merely swap in one scan — trips the breaker and it stays tripped; a
///   motor stalled in star trips the overload.</item>
/// <item>A pressure transmitter's counts track the receiver's pressure (checked
///   against this file's own arithmetic, not the helper's), rise while it
///   fills and fall while it is consumed, and read 32767 with the wire
///   broken.</item>
/// <item>A servo axis is not ready until enabled and past its brake delay,
///   starts acceleration-limited, cruises at the commanded (and then the
///   limited) velocity, reports in-position only when it is there, drops ready
///   and holds on a fault, refuses to move until an acknowledge edge arrives
///   with the cause gone — not on an ack while the fault is present, not on an
///   enable cycle — and refuses a target outside its travel.</item>
/// </list>
///
/// The limit switches need real physics — a carton riding a belt into a lever
/// — so they are sampled on real frames first. Everything else is stepped by
/// hand (<see cref="Run"/>), so a ninety-second overload trip costs no wall
/// time.
/// </summary>
public partial class IndustrialPartsSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    private readonly List<string> _failures = new();
    private int _step;
    private bool _done;

    private const double Tick = 1.0 / 60.0;

    // S7 analog representation, written out here rather than borrowed from
    // AnalogSignal, so a wrong constant there fails here.
    private const int FullScale = 27648;
    private const int WireBreakCode = 32767;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    private void Run(int ticks)
    {
        for (int i = 0; i < ticks; i++) Editor._PhysicsProcess(Tick);
    }

    private double Num(string id) => System.Convert.ToDouble(Tags.Visible(id));
    private int Count(string id) => System.Convert.ToInt32(Tags.Visible(id));
    private bool Bit(string id) => Tags.Contains(id) && Tags.Visible(id) is true;

    /// <summary>The measured numbers behind the assertions, so a PASS line in
    /// a log comes with the evidence it was earned on.</summary>
    private static void Info(string what) => GD.Print($"  info  {what}");

    private T Part<T>(string id) where T : Node3D
    {
        var node = Editor.NodeFor(id) as T;
        if (node is null) throw new System.InvalidOperationException($"'{id}' is not a {typeof(T).Name}");
        return node;
    }

    // ------------------------------------------------------------ the scene

    /// <summary>Lever x positions along the 3 m lane. The carton starts at
    /// <see cref="CartonStartX"/> and passes all four.</summary>
    private static readonly (string Id, float X)[] LaneSwitches =
    {
        ("ls", -0.3f), ("lsshort", 0.2f), ("lshigh", 0.7f), ("lsbounce", 1.2f),
    };
    private const float CartonStartX = -1.2f;
    private const float CylinderZ = 4.0f;

    private static PartInstanceData At(string id, string type, float x, float z,
                                       Dictionary<string, string>? props = null) => new()
    {
        Id = id,
        Type = type,
        Position = new[] { x, PartLayout.WorkPlaneY, z },
        Rotation = new[] { 0.0f, 0.0f, 0.0f },
        Properties = props,
    };

    private void BuildScene()
    {
        var data = new SceneData { Name = "selftest-industrial-parts" };

        data.Parts.Add(At("lane", "ConveyorBelt", 0.0f, 0.0f, new Dictionary<string, string>
        {
            ["size_x"] = "3", ["size_y"] = "0.12", ["size_z"] = "0.5", ["speed"] = "0.5",
        }));
        // Default: reaches 0.08 m into a centred carton's side.
        data.Parts.Add(At("ls", "LimitSwitch", LaneSwitches[0].X, 0.0f));
        // A lever too short to reach the lane at all.
        data.Parts.Add(At("lsshort", "LimitSwitch", LaneSwitches[1].X, 0.0f,
                          new Dictionary<string, string> { ["lever_length"] = "0.08" }));
        // A lever above a short carton's 0.10 m: it passes underneath.
        data.Parts.Add(At("lshigh", "LimitSwitch", LaneSwitches[2].X, 0.0f,
                          new Dictionary<string, string> { ["height"] = "0.20" }));
        // A bouncy one: 60 ms of chatter on every make.
        data.Parts.Add(At("lsbounce", "LimitSwitch", LaneSwitches[3].X, 0.0f,
                          new Dictionary<string, string> { ["bounce_ms"] = "60" }));

        // A moving machine part: the cylinder strokes +Z and its face plate
        // arrives at z = 4.45 at full stroke. The switch's lever tip reaches
        // down to z = 4.39, so it is pushed only in the last few centimetres.
        data.Parts.Add(At("cyl", "PneumaticCylinder", 0.0f, CylinderZ));
        data.Parts.Add(At("lscyl", "LimitSwitch", 0.0f, CylinderZ + 0.29f));

        data.Parts.Add(At("valve", "SolenoidValve", 0.0f, 8.0f));
        data.Parts.Add(At("sd", "StarDeltaStarter", 0.0f, 10.0f));
        data.Parts.Add(At("air", "PressureTransmitter", 0.0f, 12.0f));
        data.Parts.Add(At("axis", "ServoAxis", 0.0f, 14.0f));

        const string path = "user://selftest_industrialparts.json";
        using (var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }
        Editor.LoadSceneFromFile(path);
    }

    // ------------------------------------------------------------ phases

    public override void _PhysicsProcess(double delta)
    {
        if (_done) return;
        _step++;

        try
        {
            if (_step == 2) { BuildScene(); return; }
            if (_step == 4) { StartLane(); StartCylinder(); }
            if (_step >= 4 && _step <= LaneEndStep) SampleSwitches();
            if (_step == 40) CheckCylinderExtended();
            if (_step == 80) CheckCylinderRetracted();
            if (_step < LaneEndStep) return;

            _done = true;
            CheckLane();
            CheckSolenoidValve();
            CheckStarDelta();
            CheckPressureTransmitter();
            CheckServoAxis();
        }
        catch (System.Exception ex)
        {
            // Gotcha 12: a throw here is logged and the run carries on to its
            // --duration, exiting 0. Count it.
            _done = true;
            _failures.Add($"threw: {ex.GetType().Name}: {ex.Message}");
            GD.PrintErr($"  FAIL  threw at step {_step}: {ex}");
        }

        if (!_done) return;
        if (_failures.Count == 0)
        {
            GD.Print("self-test industrialparts: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test industrialparts: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    // ------------------------------------------------------------ limit switch

    /// <summary>Real frames the carton gets to ride past all four levers: 3.0 m
    /// at 0.5 m/s is six seconds, and the last lever is cleared at about
    /// five.</summary>
    private const int LaneEndStep = 345;

    private BoxPhysics? _carton;

    private sealed class SwitchLog
    {
        public bool LastNo;
        public bool LastNc = true;
        public int NoRises;
        public int NcRises;
        public bool BothMade;
        public bool BreakingChattered;
        public bool DisagreedWithLever;
        public float? FirstMakeLeadX;
        public float? FirstReleaseTrailX;
        public int FirstMakeStep = -1;
        public bool MadeTenTicksAfterFirstMake;
        public float MaxAngle;
    }

    private readonly Dictionary<string, SwitchLog> _logs = new();

    private void StartLane()
    {
        foreach (var (id, _) in LaneSwitches)
        {
            Expect(!Bit($"{id}.no") && Bit($"{id}.nc"),
                   $"{id}: at rest the NO contact is open and the NC contact made");
            _logs[id] = new SwitchLog();
        }
        _logs["lscyl"] = new SwitchLog();

        // A short carton on a running belt: the demanding case for the high
        // lever, and the ordinary case for everything else.
        Tags.Set("lane.rotate", true);
        _carton = new BoxPhysics { IsTall = false };
        Editor.GetParent().AddChild(_carton);
        _carton.GlobalPosition = new Vector3(CartonStartX, PartLayout.WorkPlaneY + 0.14f, 0.0f);
    }

    private void SampleSwitches()
    {
        foreach (var (id, log) in _logs)
        {
            var sw = Part<LimitSwitch>(id);
            bool no = Bit($"{id}.no");
            bool nc = Bit($"{id}.nc");

            if (no && !log.LastNo)
            {
                log.NoRises++;
                if (log.FirstMakeStep < 0)
                {
                    log.FirstMakeStep = _step;
                    if (_carton is not null && id != "lscyl")
                        log.FirstMakeLeadX = _carton.GlobalPosition.X + _carton.Length / 2.0f;
                }
            }
            if (nc && !log.LastNc)
            {
                log.NcRises++;
                if (log.FirstReleaseTrailX is null && _carton is not null && id != "lscyl")
                    log.FirstReleaseTrailX = _carton.GlobalPosition.X - _carton.Length / 2.0f;
            }
            if (log.FirstMakeStep >= 0 && _step == log.FirstMakeStep + 10) log.MadeTenTicksAfterFirstMake = no;

            if (no && nc) log.BothMade = true;
            // The contact that is breaking opens cleanly: NC stays open for as
            // long as the lever is actuated, NO stays open for as long as it
            // is not. Only the making contact may chatter.
            if ((sw.IsActuated && nc) || (!sw.IsActuated && no)) log.BreakingChattered = true;
            if (sw.BounceMs <= 0.0f && (no != sw.IsActuated || nc == sw.IsActuated)) log.DisagreedWithLever = true;
            log.MaxAngle = Mathf.Max(log.MaxAngle, sw.LeverAngle);

            log.LastNo = no;
            log.LastNc = nc;
        }
    }

    private void CheckLane()
    {
        Tags.Set("lane.rotate", false);
        float cartonX = _carton?.GlobalPosition.X ?? float.NaN;
        Expect(cartonX > LaneSwitches[3].X + 0.15f,
               $"the carton rode the belt past every lever (x={cartonX:0.00})");

        var ls = _logs["ls"];
        float leverX = LaneSwitches[0].X;
        Expect(ls.NoRises == 1, $"ls: one carton, one NO rising edge (got {ls.NoRises})");
        Expect(ls.FirstMakeLeadX is { } lead && Mathf.Abs(lead - leverX) < 0.05f,
               $"ls: made when the carton's leading face reached the lever at x={leverX:0.00} "
               + $"(leading face at {ls.FirstMakeLeadX?.ToString("0.000") ?? "never"})");
        Expect(ls.FirstReleaseTrailX is { } trail && Mathf.Abs(trail - leverX) < 0.05f,
               $"ls: released when the trailing face passed it "
               + $"(trailing face at {ls.FirstReleaseTrailX?.ToString("0.000") ?? "never"})");
        Expect(ls.MaxAngle >= LimitSwitch.TripAngle && ls.MaxAngle < 45.0f,
               $"ls: the lever swung by the carton's intrusion, past the trip angle (max {ls.MaxAngle:0.0} deg)");
        Expect(!ls.DisagreedWithLever, "ls: without bounce, NO and NC follow the lever exactly");
        Expect(Bit("ls.nc") && !Bit("ls.no"), "ls: back at rest once the carton has gone");

        Info($"ls: made with the leading face at x={ls.FirstMakeLeadX:0.000}, released with the trailing face "
             + $"at x={ls.FirstReleaseTrailX:0.000} (lever at {leverX:0.00}), max lever angle {ls.MaxAngle:0.0} deg");

        var shortLever = _logs["lsshort"];
        Expect(shortLever.NoRises == 0 && shortLever.MaxAngle == 0.0f,
               $"lsshort: a lever that does not reach the lane is not touched by the carton "
               + $"({shortLever.NoRises} makes, max {shortLever.MaxAngle:0.0} deg)");

        var high = _logs["lshigh"];
        Expect(high.NoRises == 0 && high.MaxAngle == 0.0f,
               $"lshigh: a short carton passes under a lever set above it "
               + $"({high.NoRises} makes, max {high.MaxAngle:0.0} deg)");

        var bounce = _logs["lsbounce"];
        Info($"lsbounce: {bounce.NoRises} NO rising edges and {bounce.NcRises} NC rising edges for one carton");
        Expect(bounce.NoRises >= 2,
               $"lsbounce: 60 ms of bounce gives a fast scan more than one NO rising edge for one carton "
               + $"(got {bounce.NoRises})");
        Expect(bounce.NcRises >= 2,
               $"lsbounce: and the NC contact chatters as it re-makes on release (got {bounce.NcRises})");
        Expect(bounce.MadeTenTicksAfterFirstMake,
               "lsbounce: the chatter settles, and the NO contact is made while the carton is still there");
        Expect(Bit("lsbounce.nc") && !Bit("lsbounce.no"), "lsbounce: settles back at rest");

        foreach (var (id, log) in _logs)
        {
            Expect(!log.BothMade, $"{id}: NO and NC are never made at the same time");
            Expect(!log.BreakingChattered, $"{id}: the breaking contact never chatters");
        }

        _carton?.QueueFree();
        _carton = null;
    }

    private void StartCylinder()
    {
        Expect(!Bit("lscyl.no") && Bit("lscyl.nc"), "lscyl: at rest with the cylinder retracted");
        Tags.Set("cyl.extend", true);
    }

    private int _cylFirstMakeStep = -1;

    private void CheckCylinderExtended()
    {
        var cyl = Part<PneumaticCylinder>("cyl");
        var log = _logs["lscyl"];
        Expect(cyl.IsExtended, $"the cylinder reached full stroke (extension {cyl.Extension:0.000} m)");
        Expect(Bit("lscyl.no") && !Bit("lscyl.nc"),
               "lscyl: a cylinder's face plate at the end of its stroke actuates the lever -- "
               + "a machine part, not a carton");
        Expect(log.NoRises == 1, $"lscyl: one stroke, one make (got {log.NoRises})");
        _cylFirstMakeStep = log.FirstMakeStep;
        Tags.Set("cyl.extend", false);
        Tags.Set("cyl.retract", true);
    }

    private void CheckCylinderRetracted()
    {
        var cyl = Part<PneumaticCylinder>("cyl");
        Expect(cyl.IsRetracted, "the cylinder retracted");
        Expect(!Bit("lscyl.no") && Bit("lscyl.nc"), "lscyl: releases when the plate draws back");
        // The stroke takes about 27 ticks at 0.9 m/s from step 5; the lever is
        // only reached in the last few centimetres, so the make comes late.
        Expect(_cylFirstMakeStep >= 20,
               $"lscyl: made only near the end of the stroke, not as soon as the rod moved "
               + $"(first make at step {_cylFirstMakeStep})");
        Tags.Set("cyl.retract", false);
        Info($"lscyl: first made at step {_cylFirstMakeStep}; the stroke began at step 5");
    }

    // ------------------------------------------------------------ valve

    private void CheckSolenoidValve()
    {
        var valve = Part<SolenoidValve>("valve");   // 2.0 s travel

        Expect(Bit("valve.closed") && !Bit("valve.opened"), "a placed valve starts shut");

        Tags.Set("valve.open", true);
        Run(1);
        Expect(Bit("valve.closed"), "one tick after the solenoid is energised it has not left its seat");
        Run(59);
        Expect(!Bit("valve.closed") && !Bit("valve.opened"),
               $"mid-stroke neither feedback is made -- !closed does not mean open (opening {valve.Opening:0.00})");
        Run(54);
        Expect(!Bit("valve.opened"), $"at 1.9 s of a 2 s stroke it is still not open (opening {valve.Opening:0.00})");
        Run(12);
        Expect(Bit("valve.opened") && !Bit("valve.closed"), "opened arrives once the travel time has passed");

        Tags.Set("valve.open", false);
        Run(60);
        Expect(!Bit("valve.closed") && !Bit("valve.opened"), "closing, it is neither open nor closed half way");
        Run(70);
        Expect(Bit("valve.closed"), "the spring returns it shut with the solenoid dropped");

        // Stuck shut: the discrepancy the program exists to catch.
        Tags.Force("valve.fault", true);
        Tags.Set("valve.open", true);
        Run(60 * 5);
        Expect(Bit("valve.closed") && !Bit("valve.opened"),
               "stuck: commanded open for 5 s, the feedback still reads closed");
        Expect(valve.Opening == 0.0f, $"and the stem has not moved ({valve.Opening:0.000})");

        // Stuck half way: neither feedback, forever, whichever way it is told.
        Tags.ClearForce("valve.fault");
        Run(60);
        Tags.Force("valve.fault", true);
        Run(60 * 5);
        Expect(!Bit("valve.closed") && !Bit("valve.opened"),
               $"stuck mid-stroke: neither feedback for 5 s (opening {valve.Opening:0.00})");
        Tags.Set("valve.open", false);
        Run(60 * 3);
        Expect(!Bit("valve.closed") && !Bit("valve.opened"), "and the spring cannot shut a seized stem either");

        Tags.ClearForce("valve.fault");
        Run(60 * 3);
        Expect(Bit("valve.closed"), "freed, it follows the command again");
    }

    // ------------------------------------------------------------ star-delta

    private void Coils(bool main, bool star, bool delta)
    {
        Tags.Set("sd.main", main);
        Tags.Set("sd.star", star);
        Tags.Set("sd.delta", delta);
    }

    private void CheckStarDelta()
    {
        var sd = Part<StarDeltaStarter>("sd");   // 20 A, 60 % load, 2 s inertia
        float lockedRotorAmps = 6.0f * sd.FullLoadAmps;

        Expect(Bit("sd.breaker") && Bit("sd.overload"), "a placed starter's breaker and overload read healthy (NC)");

        // Start in star.
        Coils(true, true, false);
        Run(1);
        Expect(!Bit("sd.mainaux") && !Bit("sd.staraux"),
               "the auxiliary contacts are still open on the scan the coils are energised");
        Run(2);
        Expect(Bit("sd.mainaux") && Bit("sd.staraux") && !Bit("sd.deltaaux"),
               "and close once the contactors have pulled in");
        float starInrush = (float)Num("sd.current");
        Expect(starInrush > 0.25f * lockedRotorAmps && starInrush < 0.40f * lockedRotorAmps,
               $"star starting current is about a third of direct-on-line: {starInrush:0.0} A "
               + $"against {lockedRotorAmps:0} A locked-rotor");

        Run(60 * 6);
        float starSpeed = (float)Num("sd.speed");
        Info($"sd: star inrush {starInrush:0.0} A, {starSpeed:0.0} % speed and {Num("sd.current"):0.0} A after 6 s in star");
        Expect(starSpeed > 80.0f, $"a lightly loaded motor runs up in star ({starSpeed:0.0} %)");
        Expect(Num("sd.current") < starInrush / 2.0, $"and its current falls as it does ({Num("sd.current"):0.0} A)");

        // A clean changeover: drop star, wait for its aux contact, then delta.
        Tags.Set("sd.star", false);
        int waited = 0;
        while (Bit("sd.staraux") && waited < 30) { Run(1); waited++; }
        Expect(waited >= 2, $"the star contact stays closed for its drop-out time ({waited} ticks)");
        Tags.Set("sd.delta", true);
        float peak = 0.0f;
        for (int i = 0; i < 60 * 3; i++) { Run(1); peak = Mathf.Max(peak, (float)Num("sd.current")); }
        Info($"sd: staraux fell {waited} ticks after the star coil; changeover peak {peak:0.0} A, then "
             + $"{Num("sd.current"):0.0} A at {Num("sd.speed"):0.0} % in delta");
        Expect(Bit("sd.breaker"), "a changeover that waits for staraux to fall does not trip the breaker");
        Expect(Bit("sd.deltaaux") && !Bit("sd.staraux"), "and runs in delta");
        Expect(Num("sd.speed") > 95.0, $"up to full speed ({Num("sd.speed"):0.0} %)");
        Expect(peak < 0.6f * lockedRotorAmps && peak > starInrush * 0.5f,
               $"the changeover costs a current step, but well short of locked-rotor ({peak:0.0} A)");
        Expect(Num("sd.current") < 1.1 * sd.FullLoadAmps, $"and settles to a running current ({Num("sd.current"):0.0} A)");

        Coils(false, false, false);
        Run(60 * 8);
        Expect(Num("sd.speed") < starSpeed, $"it coasts down with the contactors out ({Num("sd.speed"):0.0} %)");

        // Star and delta together: a short circuit, main contactor or not.
        Coils(false, true, true);
        Run(5);
        Expect(!Bit("sd.breaker"),
               "star and delta closed together trip the breaker -- even with the main contactor out");
        Coils(false, false, false);
        Run(60);
        Expect(!Bit("sd.breaker"), "and it stays tripped with both coils dropped: it is latched");
        Coils(true, true, false);
        Run(60 * 2);
        Expect(Num("sd.current") == 0.0 && Num("sd.speed") < 1.0,
               $"a tripped breaker powers nothing ({Num("sd.current"):0.0} A, {Num("sd.speed"):0.0} %)");
        Coils(false, false, false);
        Run(10);
        sd.ResetBreaker();
        Run(1);
        Expect(Bit("sd.breaker"), "the hand reset closes it again");

        // The same short, from a program that swaps the two in one scan: delta
        // closes 30 ms after its coil, star is still arcing until 45 ms.
        Coils(true, true, false);
        Run(60 * 6);
        Coils(true, false, true);
        Run(5);
        Expect(!Bit("sd.breaker"),
               "dropping star and energising delta in the same scan trips it too: a contactor opens slower than one closes");
        Coils(false, false, false);
        Run(60 * 8);
        sd.ResetBreaker();

        // And it is the same short at four times speed, where one tick is
        // longer than the whole overlap.
        sd.ResetPart(new PartReset(null, "sd"));
        for (int i = 0; i < 90; i++) sd.Step(true, true, false, 4.0f / 60.0f);
        sd.Step(true, false, true, 4.0f / 60.0f);
        Expect(sd.IsBreakerTripped, "a same-scan changeover still trips when one tick is 67 ms long");
        sd.ResetPart(new PartReset(null, "sd"));
        sd.ResetBreaker();

        // A motor stalled in star: its star pull-out torque is under a 150 %
        // load, so it never runs up, and the overload -- in the winding, which
        // in star carries the whole line current -- trips it out.
        sd.LoadPercent = 150.0f;
        Coils(true, true, false);
        Run(60 * 120);
        Info($"sd: at 150 % load in star after 120 s: {Num("sd.speed"):0.0} % speed, overload "
             + (Bit("sd.overload") ? "healthy" : "tripped"));
        Expect(!Bit("sd.overload"), $"a motor stalled in star trips its overload ({Num("sd.speed"):0.0} % speed)");
        Expect(!Bit("sd.mainaux") && !Bit("sd.staraux"),
               "and the overload's contact drops every contactor, still commanded");
        Expect(Bit("sd.breaker"), "without troubling the breaker, which is for shorts");
        Coils(false, false, false);
        sd.LoadPercent = 60.0f;
        sd.ResetBreaker();
        Run(1);
        Expect(Bit("sd.overload"), "the reset clears the overload too");
    }

    // ------------------------------------------------------------ transmitter

    private int ExpectedCounts(float bar, float min, float max) =>
        (int)System.Math.Round(FullScale * (bar - min) / (max - min), System.MidpointRounding.AwayFromZero);

    private void CheckPressureTransmitter()
    {
        var air = Part<PressureTransmitter>("air");   // 0-10 bar, 4-20 mA

        Expect(Tags.Get("air.pressure")?.Type == TagType.Int,
               $"the transmitter publishes raw counts as an INT, never bar (got {Tags.Get("air.pressure")?.Type})");
        Expect(Tags.Contains("air.wirebreak"), "4-20 mA by default, so the wire break is on the bus");
        Expect(Count("air.pressure") == 0, $"an empty receiver reads 0 counts (4 mA), got {Count("air.pressure")}");

        Tags.Set("air.supply", true);
        int last = -1;
        bool rising = true, tracking = true;
        for (int i = 0; i < 10; i++)
        {
            Run(30);
            int counts = Count("air.pressure");
            if (counts <= last) rising = false;
            if (System.Math.Abs(counts - ExpectedCounts(air.Pressure, 0.0f, 10.0f)) > 1)
            {
                tracking = false;
                GD.PrintErr($"    {air.Pressure:0.000} bar published as {counts}");
            }
            last = counts;
        }
        Expect(rising, "the counts rise while the supply valve fills the receiver");
        Expect(tracking, "and every reading is the pressure on a 0-10 bar, 0-27648 scale");

        Run(60 * 20);
        float full = air.Pressure;
        Info($"air: {full:0.000} bar with the supply open reads {Count("air.pressure")} counts, "
             + $"transmitter showing {air.LoopCurrent:0.00} mA");
        Expect(full > 6.0f && full < 6.6f,
               $"with the valve open it settles where supply balances consumption ({full:0.00} bar, expected ~6.4)");
        int fullCounts = Count("air.pressure");
        Expect(System.Math.Abs(fullCounts - ExpectedCounts(full, 0.0f, 10.0f)) <= 1,
               $"{full:0.000} bar reads {fullCounts} counts, expected {ExpectedCounts(full, 0.0f, 10.0f)}");

        Tags.Set("air.supply", false);
        Run(60 * 3);
        Expect(Count("air.pressure") < fullCounts * 0.8,
               $"with the valve shut the consumption bleeds it down ({air.Pressure:0.00} bar, {Count("air.pressure")} counts)");

        Tags.Force("air.wirebreak", true);
        Run(1);
        Expect(Count("air.pressure") == WireBreakCode,
               $"a broken 4-20 mA wire reads {WireBreakCode} (got {Count("air.pressure")})");
        Expect(air.LoopCurrent == 0.0f, "and the loop-powered transmitter has no current to show");
        Tags.ClearForce("air.wirebreak");
        Run(1);
        Expect(System.Math.Abs(Count("air.pressure") - ExpectedCounts(air.Pressure, 0.0f, 10.0f)) <= 1,
               "and mended, it reads the pressure again");
    }

    // ------------------------------------------------------------ servo

    private void Pulse(string id)
    {
        Tags.Set(id, true);
        Run(2);
        Tags.Set(id, false);
        Run(1);
    }

    /// <summary>Run until in position or out of time, and report whether
    /// in-position was ever claimed away from the target.</summary>
    private (bool arrived, bool lied, float overshoot) RunToTarget(ServoAxis axis, float target, int maxTicks)
    {
        bool lied = false;
        float overshoot = 0.0f;
        float start = axis.ActualPosition;
        float direction = Mathf.Sign(target - start);
        for (int i = 0; i < maxTicks; i++)
        {
            Run(1);
            float pos = (float)Num("axis.position");
            overshoot = Mathf.Max(overshoot, (pos - target) * direction);
            if (Bit("axis.inposition") && Mathf.Abs(pos - target) > axis.InPositionWindow) lied = true;
            if (Bit("axis.inposition") && Mathf.Abs(pos - target) <= axis.InPositionWindow) return (true, lied, overshoot);
        }
        return (false, lied, overshoot);
    }

    private void CheckServoAxis()
    {
        var axis = Part<ServoAxis>("axis");   // 1000 mm, 500 mm/s, 2000 mm/s^2

        Expect(!Bit("axis.ready") && !Bit("axis.inposition") && Num("axis.position") == 0.0,
               "a placed axis is not ready, not in position, at 0 mm");

        Tags.Set("axis.velocity", 250.0);
        Tags.Set("axis.target", 500.0);
        Run(30);
        Expect(Num("axis.position") == 0.0, "with enable off it does not move, whatever the target says");

        Tags.Set("axis.target", 0.0);
        Tags.Set("axis.enable", true);
        Run(1);
        Expect(!Bit("axis.ready"), "not ready on the scan it is enabled: the brake is still releasing");
        Run(10);
        Expect(Bit("axis.ready"), "ready once the enable delay has passed");
        Expect(Bit("axis.inposition"), "and in position at the target it already holds");

        Tags.Set("axis.target", 500.0);
        Run(1);
        Expect(!Bit("axis.inposition"), "in-position drops once the drive has taken the new target");
        Run(5);   // 0.1 s into the move
        float early = (float)Num("axis.position");
        Expect(early > 0.0f && early < 15.0f,
               $"it starts acceleration-limited: {early:0.0} mm after 0.1 s (1/2 a t^2 = 10, a velocity step would be 25)");
        Run(30);
        float p1 = (float)Num("axis.position");
        Run(30);
        float cruise = ((float)Num("axis.position") - p1) / 0.5f;
        Info($"axis: {early:0.00} mm after 0.1 s, cruising at {cruise:0.0} mm/s");
        Expect(Mathf.Abs(cruise - 250.0f) < 5.0f, $"and cruises at the commanded 250 mm/s ({cruise:0.0})");

        var (arrived, lied, overshoot) = RunToTarget(axis, 500.0f, 60 * 4);
        Info($"axis: arrived {(arrived ? "yes" : "no")} at {Num("axis.position"):0.000} mm, overshoot {overshoot:0.000} mm");
        Expect(arrived, $"it arrives at 500 mm and reports in position ({Num("axis.position"):0.00} mm)");
        Expect(!lied, "in-position was never true anywhere but at the target");
        Expect(overshoot <= axis.InPositionWindow, $"without overshooting ({overshoot:0.000} mm)");

        // The velocity is limited to the axis maximum.
        Tags.Set("axis.velocity", 5000.0);
        Tags.Set("axis.target", 0.0);
        Run(30);
        p1 = (float)Num("axis.position");
        Run(15);
        float fast = (p1 - (float)Num("axis.position")) / 0.25f;
        Expect(Mathf.Abs(fast - axis.MaxVelocity) < 10.0f,
               $"5000 mm/s is limited to the axis maximum of {axis.MaxVelocity:0} ({fast:0.0})");
        (arrived, _, _) = RunToTarget(axis, 0.0f, 60 * 4);
        Expect(arrived, "and it arrives back at 0 mm");

        // A fault mid-move: ready drops, the carriage stops and holds.
        Tags.Set("axis.velocity", 250.0);
        Tags.Set("axis.target", 900.0);
        Run(60);
        Tags.Force("axis.fault", true);
        Run(1);
        Expect(!Bit("axis.ready") && Bit("axis.error"), "a drive fault drops ready and latches the error");
        Expect(!Bit("axis.inposition"), "and it is not in position");
        Run(30);
        float stopped = (float)Num("axis.position");
        Info($"axis: faulted while moving to 900 mm, stopped at {stopped:0.00} mm");
        Run(60);
        Expect(Mathf.Abs((float)Num("axis.position") - stopped) < 0.01f && stopped < 890.0f,
               $"it stops short of its target and holds ({stopped:0.0} mm)");

        // The cause goes away. The drive does not move until acknowledged.
        Tags.ClearForce("axis.fault");
        Run(60);
        Expect(Bit("axis.error") && !Bit("axis.ready"), "the error stays latched after the fault has cleared");
        Expect(Mathf.Abs((float)Num("axis.position") - stopped) < 0.01f, "and it refuses to move");

        // An acknowledge while the fault is still present acknowledges nothing.
        Tags.Force("axis.fault", true);
        Pulse("axis.ack");
        Expect(Bit("axis.error"), "an ack while the fault is still there does not clear it");
        Tags.ClearForce("axis.fault");

        // Cycling enable is not an acknowledge.
        Tags.Set("axis.enable", false);
        Run(5);
        Tags.Set("axis.enable", true);
        Run(20);
        Expect(Bit("axis.error") && !Bit("axis.ready"), "cycling enable does not clear the error either");

        Pulse("axis.ack");
        Expect(!Bit("axis.error"), "a rising edge of ack, with the cause gone, clears it");
        Run(10);
        Expect(Bit("axis.ready"), "and the drive is ready again");
        (arrived, _, _) = RunToTarget(axis, 900.0f, 60 * 6);
        Expect(arrived, $"and completes the move it still has a target for ({Num("axis.position"):0.0} mm)");

        // Commands it cannot carry out are refused, not guessed at.
        Tags.Set("axis.target", 1500.0);
        Run(2);
        Expect(Bit("axis.error") && !Bit("axis.ready"), "a target outside the travel latches an error");
        Run(60);
        Expect(Num("axis.position") <= axis.StrokeMm + 1e-3, "and the carriage does not leave its travel");
        Pulse("axis.ack");
        Expect(Bit("axis.error"), "an ack with the target still out of range changes nothing");
        Tags.Set("axis.target", 800.0);
        Pulse("axis.ack");
        Expect(!Bit("axis.error"), "with a reachable target the ack clears it");

        Tags.Set("axis.velocity", 0.0);
        Tags.Set("axis.target", 100.0);
        Run(12);
        Expect(Bit("axis.error"), "a move asked for at zero velocity is refused with an error");

        Tags.Set("axis.enable", false);
        Run(1);
        Expect(!Bit("axis.ready"), "enable off drops ready at once");
    }
}
