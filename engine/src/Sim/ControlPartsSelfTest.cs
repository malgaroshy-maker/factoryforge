using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check that the six control and process parts do what their tags
/// claim:
///
/// <code>godot --headless --path engine -- --self-test=controlparts</code>
///
/// Every assertion here is about an <em>effect</em>. "The tag exists" is true of
/// any part the moment it is placed and proves nothing — the weighing conveyor's
/// fault contact existed, appeared in the inspector and could be forced for five
/// releases while doing absolutely nothing. So what is asserted is that the
/// contactor's auxiliary contact does not close on the scan the coil is
/// energised, that a thermal overload trips on current *and time* and stays
/// tripped, that a safety relay refuses to restart on a held reset, that it
/// latches a fault on a channel that does not follow its partner, that muting a
/// scanner stops working once it has been held too long, that a double-acting
/// cylinder reports neither reed mid-stroke and holds its spool with both coils
/// dropped, that a pump actually raises a tank's level and a flow meter reads
/// what the pump is delivering, and that the totaliser's reset is a level rather
/// than an edge.
///
/// The clock is hand-turned (<see cref="Run"/>) rather than real, so a
/// thirty-second overload trip costs no wall time. None of these parts reads an
/// <c>Area3D</c>, which is what forces <c>--self-test=newparts</c> to use real
/// ticks for its scanner: the area scanner here compares world positions
/// directly, so a carton parked in its field is seen on the very next
/// dispatch.
/// </summary>
public partial class ControlPartsSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    private readonly List<string> _failures = new();
    private int _step;
    private bool _done;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    private const double Tick = 1.0 / 60.0;

    private void Run(int ticks)
    {
        for (int i = 0; i < ticks; i++) Editor._PhysicsProcess(Tick);
    }

    private double Num(string id) => System.Convert.ToDouble(Tags.Visible(id));
    private bool Bit(string id) => Tags.Contains(id) && Tags.Visible(id) is true;

    private T Part<T>(string id) where T : Node3D
    {
        var node = Editor.NodeFor(id) as T;
        if (node is null) throw new System.InvalidOperationException($"'{id}' is not a {typeof(T).Name}");
        return node;
    }

    public override void _PhysicsProcess(double delta)
    {
        // Two ticks of grace: parts build their geometry in _Ready, which does
        // not run until the node has been in the tree for a frame, and some of
        // what is asserted below reads that geometry back.
        if (++_step == 2) { BuildScene(); return; }
        if (_step != 4 || _done) return;
        _done = true;

        try
        {
            CheckStarterSealsInLate();
            CheckOverloadTripsOnCurrentAndTime();
            CheckStarterPowersItsMotor();
            CheckRelayRefusesAutomaticRestart();
            CheckRelayCatchesAStuckChannel();
            CheckRelayInterlocksWithoutCommanding();
            CheckScannerFieldsAndMutingTimeout();
            CheckCylinderSpoolAndReeds();
            CheckPumpFillsTheTank();
            CheckFlowMeterReadsAndTotalises();
        }
        catch (System.Exception ex)
        {
            // Gotcha 12: a throw here is logged by Godot and the run continues
            // to its --duration, so without this the report never happens and
            // the exit code is success.
            _failures.Add(ex.Message);
            GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
        }

        if (_failures.Count == 0)
        {
            GD.Print("self-test controlparts: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test controlparts: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    // ---------- the scene

    private void BuildScene()
    {
        var data = new SceneData { Name = "selftest-control-parts" };

        // The motor the starter powers, and the starter itself. The load is set
        // above the trip setting so the overload has something to do.
        Add(data, "belt", "ConveyorBelt", -6.0f);
        Add(data, "starter", "MotorStarter", -4.5f, new Dictionary<string, string>
        {
            ["load_tag"] = "belt.rotate",
            ["load_percent"] = "300",
            ["trip_percent"] = "115",
            // A very fast class, so the trip lands inside a hand-turned
            // thirty seconds instead of the two minutes a Class 10 would take.
            ["trip_time"] = "2",
            ["pull_in"] = "0.1",
        });

        // The relay watches two contacts the test drives directly, and
        // interlocks the starter's coil.
        Add(data, "guard", "SafetyGate", -3.0f);
        Add(data, "panel", "ButtonPanel", -1.5f);
        Add(data, "relay", "SafetyRelay", 0.0f, new Dictionary<string, string>
        {
            ["channel_a_tag"] = "guard.closed",
            ["channel_b_tag"] = "panel.estop",
            ["sync_window"] = "0.2",
            ["load_tag"] = "starter.coil",
        });

        Add(data, "scanner", "AreaScanner", 2.0f, new Dictionary<string, string>
        {
            ["stop_radius"] = "1.0",
            ["warn_radius"] = "2.0",
            ["scan_arc"] = "360",
            ["mute_limit"] = "2",
        });

        Add(data, "cylinder", "PneumaticCylinder", 4.0f, new Dictionary<string, string>
        {
            ["stroke"] = "0.4",
            ["rod_speed"] = "0.4",
        });

        // Pump, tank and meter close enough together to be plumbed to each
        // other. The tank's own valves stay shut throughout, so every litre the
        // level gains came from the pump.
        Add(data, "tank", "LevelTank", 6.0f, new Dictionary<string, string>
        {
            ["capacity"] = "100",
        });
        Add(data, "pump", "DosingPump", 6.6f, new Dictionary<string, string>
        {
            ["rated_flow"] = "60",
            ["reach"] = "1.5",
            ["ramp_rate"] = "400",
        });
        Add(data, "meter", "FlowMeter", 7.2f, new Dictionary<string, string>
        {
            ["reach"] = "1.2",
            ["damping"] = "0",
        });

        const string path = "user://selftest_controlparts.json";
        using (var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }
        Editor.LoadSceneFromFile(path);
    }

    private static void Add(SceneData data, string id, string type, float x,
                            Dictionary<string, string>? properties = null) =>
        data.Parts.Add(new PartInstanceData
        {
            Id = id,
            Type = type,
            Position = new[] { x, PartLayout.WorkPlaneY, -3.0f },
            Rotation = new[] { 0.0f, 0.0f, 0.0f },
            Properties = properties,
        });

    // ---------- MotorStarter

    /// <summary>
    /// The auxiliary contact must not close on the same scan the coil is
    /// energised.
    ///
    /// This is the whole reason a seal-in circuit works at all: the rung is
    /// "Start OR Aux, AND Stop, AND Overload", and if Aux closed instantly a
    /// student could write a latch that appeared to seal when it was really the
    /// Start bit still being held. A contactor takes tens of milliseconds and
    /// this one does too.
    /// </summary>
    private void CheckStarterSealsInLate()
    {
        var starter = Part<MotorStarter>("starter");

        // The relay interlocks this starter's coil and powers up de-energised,
        // holding it off -- which is correct, and is why this has to close the
        // relay before it can say anything about the contactor. A safety relay
        // that let a machine start before it had been reset would be the first
        // thing wrong with the scene.
        Run(2);
        Expect(Tags.Visible("starter.coil") is false,
               "an un-reset safety relay holds the starter's coil off from power-up");
        Tags.Set("relay.reset", true);
        Run(2);
        Tags.Set("relay.reset", false);
        Run(2);
        Expect(Bit("relay.k1"), "and closing the relay hands the coil back to the controller");

        Tags.Set("starter.coil", true);
        Run(1);

        Expect(!Bit("starter.aux"),
               "the auxiliary contact is still open on the scan the coil is energised");
        Expect(!starter.IsClosed, "and the contactor has not pulled in yet");

        Run(12);   // 0.2 s, past the 0.1 s pull-in
        Expect(Bit("starter.aux"), "and closes once the contactor has pulled in");
        Expect(Num("starter.current") > 0.0, "with current flowing in the motor");
    }

    /// <summary>
    /// A thermal overload trips on current AND time, and stays tripped.
    ///
    /// Both halves matter. If it tripped on current alone, every start would
    /// trip it -- the inrush here is six times rated. If it healed itself, it
    /// would be a fuse that grows back, and the reset button on the front of
    /// every real overload would have nothing to do.
    /// </summary>
    private void CheckOverloadTripsOnCurrentAndTime()
    {
        var starter = Part<MotorStarter>("starter");

        Expect(Bit("starter.overload"),
               "the overload has not tripped on the starting inrush alone");
        Expect(starter.ThermalState > 0.0f,
               $"but the element is heating ({starter.ThermalState:0.000})");

        // 300 % load against a 115 % trip setting: well over, so the element
        // fills, and not so far over that it is instantaneous.
        Run(60 * 30);

        Expect(!Bit("starter.overload"),
               $"a sustained overload trips the element "
               + $"(thermal {starter.ThermalState:0.00}, {starter.Current:0.0} A)");
        Expect(!Bit("starter.aux"), "and the contactor drops out with it");

        // Still commanded, and still refusing. A protective device that let the
        // standing command back in would protect nothing.
        Run(60);
        Expect(!Bit("starter.aux"),
               "and stays out while the coil is still commanded -- a trip is latched");

        // Take the mechanical load back off before resetting. An overload
        // reset against a motor that is still overloaded trips again, which is
        // correct and is also not what the rest of this test is about.
        starter.LoadPercent = 80.0f;
        starter.ResetOverload();
        Run(20);
        Expect(Bit("starter.overload"), "the reset button clears the element");
        Expect(Bit("starter.aux"), "and the contactor pulls back in");
    }

    /// <summary>The starter commands: the belt runs because the contactor is
    /// in, not because anything wrote to the belt.</summary>
    private void CheckStarterPowersItsMotor()
    {
        var belt = Part<ConveyorBelt>("belt");

        Expect(belt.IsRunning,
               "the motor runs because its contactor is in, without anybody writing belt.rotate");

        // Cut the coil. The belt has to stop even though nothing has touched
        // its own tag -- which is the entire point of having a starter.
        Tags.Set("starter.coil", false);
        Run(4);
        Expect(!belt.IsRunning, "and stops the moment the contactor drops out");
        Expect(belt.ConstantLinearVelocity.Length() < 0.001f,
               $"with the deck surface actually still ({belt.ConstantLinearVelocity.Length():0.000} m/s)");
    }

    // ---------- SafetyRelay

    /// <summary>
    /// A held reset must not restart the machine.
    ///
    /// Automatic restart is the failure guarding exists to prevent and the one
    /// a student builds by accident: close the guard and the machine goes. The
    /// relay takes the rising edge only, so a reset left high energises nothing
    /// when the channels come back.
    /// </summary>
    private void CheckRelayRefusesAutomaticRestart()
    {
        var relay = Part<SafetyRelay>("relay");

        // Open the guard: both channels drop together, so no discrepancy.
        Tags.Force("guard.closed", false);
        Tags.Force("panel.estop", false);
        Run(4);
        Expect(!Bit("relay.k1") && !Bit("relay.k2"), "opening the channels drops both contacts");

        // Hold the reset high, then bring the channels back. Nothing may
        // energise: the edge happened while the channels were open.
        Tags.Set("relay.reset", true);
        Run(4);
        Tags.Force("guard.closed", true);
        Tags.Force("panel.estop", true);
        Run(30);

        Expect(!Bit("relay.k1"),
               "a reset held high does not restart when the channels return -- that would be "
               + "automatic restart");
        Expect(!relay.IsEnergised, "and the relay agrees it is still open");

        // Release and press. Now it may energise.
        Tags.Set("relay.reset", false);
        Run(2);
        Tags.Set("relay.reset", true);
        Run(2);
        Expect(Bit("relay.k1") && Bit("relay.k2"),
               "a fresh rising edge, with the channels healthy, closes both contacts");
        Tags.Set("relay.reset", false);
        Run(2);
        Expect(Bit("relay.k1"), "and they stay closed when the reset is released");
    }

    /// <summary>
    /// One channel opening while the other does not is a welded contact or a
    /// cut wire, and a single-channel reading cannot see it at all. That is the
    /// entire argument for dual-channel wiring, and it is only true if the
    /// relay actually cross-monitors.
    /// </summary>
    private void CheckRelayCatchesAStuckChannel()
    {
        var relay = Part<SafetyRelay>("relay");

        Tags.Force("guard.closed", false);       // channel A opens
        Run(6);                                  // 0.1 s -- inside the window
        Expect(!relay.IsFaulted, "a channel that is merely slow is not a fault yet");
        Expect(!Bit("relay.k1"), "but the contacts are already open, which is the safe order");

        Run(30);                                 // past the 0.2 s window
        Expect(relay.IsFaulted,
               "a channel still disagreeing past the sync window is a latched fault");
        Expect(Bit("relay.fault"), "and the relay says so on its own tag");

        // A reset cannot clear a fault the circuit is still showing.
        Tags.Force("guard.closed", true);
        Run(2);
        Tags.Set("relay.reset", true);
        Run(2);
        Tags.Set("relay.reset", false);
        Run(2);
        Expect(!relay.IsFaulted, "with both channels back, a reset clears the fault");
        Expect(Bit("relay.k1"), "and the same edge closes the contacts");
    }

    /// <summary>The relay permits and never commands: it holds its load tag
    /// false while open, and hands it back when closed. A safety contact in
    /// series with a coil cannot start anything.</summary>
    private void CheckRelayInterlocksWithoutCommanding()
    {
        // With the relay closed, the controller has starter.coil back.
        Tags.Set("starter.coil", true);
        Run(20);
        Expect(Bit("starter.aux"),
               "with the relay closed, the controller's own start command reaches the contactor");

        // Drop a channel. The relay opens and holds the coil off, whatever the
        // controller is writing.
        Tags.Force("panel.estop", false);
        Tags.Force("guard.closed", false);
        Run(6);
        Expect(!Bit("relay.k1"), "dropping the channels opens the relay");
        Expect(!Bit("starter.aux"),
               "and the coil is held off although the controller is still commanding it");
        Expect(Tags.Visible("starter.coil") is false,
               "the coil tag itself reads false -- the relay is holding the circuit, visibly");

        // And it cannot start anything on its own: closing the relay again
        // leaves the machine exactly where the controller's command puts it.
        Tags.Set("starter.coil", false);
        Tags.Force("panel.estop", true);
        Tags.Force("guard.closed", true);
        Run(2);
        Tags.Set("relay.reset", true);
        Run(2);
        Tags.Set("relay.reset", false);
        Run(20);
        Expect(Bit("relay.k1"), "the relay closes again");
        Expect(!Bit("starter.aux"),
               "and starts nothing -- a permissive is not a command");
    }

    // ---------- AreaScanner

    /// <summary>
    /// Two fields, and muting that expires.
    ///
    /// The timeout is the assertion worth having. Muting is a real hole in a
    /// guard; what stops it becoming a permanent one is that muting held longer
    /// than a pallet takes is muting somebody has taped on. A mute modelled as
    /// a plain bridge would pass every other check here and teach a defeat.
    /// </summary>
    private void CheckScannerFieldsAndMutingTimeout()
    {
        var scanner = Part<AreaScanner>("scanner");

        Expect(Bit("scanner.stop"), "an empty field reads clear");
        Expect(!Bit("scanner.warn"), "and nothing in the warning field either");

        // A carton 1.6 m away: inside the 2.0 m warning field, outside the
        // 1.0 m protective one.
        var carton = new BoxPhysics { Name = "SelfTestCarton" };
        AddChild(carton);
        carton.Position = scanner.GlobalPosition + new Vector3(1.6f, 0.0f, 0.0f);
        Run(2);

        Expect(Bit("scanner.warn"), "a carton in the warning field raises the warning");
        Expect(Bit("scanner.stop"),
               "and does not stop anything -- a scanner that stopped on the warning field "
               + "would be switched off within a week");

        // Now into the protective field.
        carton.Position = scanner.GlobalPosition + new Vector3(0.6f, 0.0f, 0.0f);
        Run(2);
        Expect(!Bit("scanner.stop"), "a carton in the protective field stops the machine");

        // Mute it. The field is bridged and the pallet may pass.
        Tags.Set("scanner.mute", true);
        Run(2);
        Expect(Bit("scanner.stop"), "muting bridges the protective field");
        Expect(Bit("scanner.muted"), "and says so, because a bridged guard must be visible");

        // Hold it past the 2 s limit. The scanner stops honouring it.
        Run(60 * 3);
        Expect(!Bit("scanner.stop"),
               $"muting held past its limit stops being honoured "
               + $"(held {scanner.MuteHeldFor:0.0} s against a 2.0 s limit)");
        Expect(!Bit("scanner.muted"), "and the muting lamp goes out with it");

        // Releasing the mute resets the clock, so the next pallet still gets
        // its window. A latched timeout would stop the line until a power
        // cycle, which nobody would accept and everybody would defeat.
        Tags.Set("scanner.mute", false);
        Run(2);
        Tags.Set("scanner.mute", true);
        Run(2);
        Expect(Bit("scanner.stop"), "releasing and re-requesting the mute gives a fresh window");

        Tags.Set("scanner.mute", false);
        carton.QueueFree();
        Run(2);
    }

    // ---------- PneumaticCylinder

    /// <summary>
    /// A 5/2 valve with no spring, and two reeds with a gap between them.
    ///
    /// Three things the single-acting pusher cannot teach: that neither reed is
    /// made in the middle, that dropping both coils does not bring the rod
    /// back, and that energising both moves nothing.
    /// </summary>
    private void CheckCylinderSpoolAndReeds()
    {
        var cylinder = Part<PneumaticCylinder>("cylinder");

        Expect(Bit("cylinder.retracted") && !Bit("cylinder.extended"),
               "the cylinder parks retracted, in the state its geometry shows");

        Tags.Set("cylinder.extend", true);
        Run(12);   // 0.2 s at 0.4 m/s = 0.08 m of a 0.40 m stroke

        Expect(!Bit("cylinder.extended") && !Bit("cylinder.retracted"),
               $"mid-stroke NEITHER reed is made -- '!extended' is not 'retracted' "
               + $"(at {cylinder.Extension:0.000} m of {cylinder.StrokeLength:0.000})");

        // Drop the coil. A spring-return cylinder would come back; this one
        // keeps going, because the spool is still ported to extend.
        Tags.Set("cylinder.extend", false);
        Run(2);
        float wasAt = cylinder.Extension;
        Run(30);
        Expect(cylinder.Extension > wasAt,
               $"with both coils dropped the rod keeps going -- a 5/2 valve has no spring "
               + $"({wasAt:0.000} m -> {cylinder.Extension:0.000} m)");

        Run(60);
        Expect(Bit("cylinder.extended"), "and reaches the extend reed");

        // Both coils at once: the solenoids fight and the spool does not move,
        // so the rod stays where the last valid command left it.
        Tags.Set("cylinder.extend", true);
        Tags.Set("cylinder.retract", true);
        Run(60);
        Expect(Bit("cylinder.extended"),
               "energising both coils moves nothing -- the spool holds where it was");

        Tags.Set("cylinder.extend", false);
        Run(90);
        Expect(Bit("cylinder.retracted"), "and the retract coil alone brings it home");
        Tags.Set("cylinder.retract", false);

        // A seized cylinder stops where it stands, not somewhere safe.
        Tags.Set("cylinder.extend", true);
        Run(20);
        Tags.Force("cylinder.fault", true);
        float seizedAt = cylinder.Extension;
        Run(90);
        Expect(Mathf.IsEqualApprox(cylinder.Extension, seizedAt),
               $"a seized cylinder stops where it stands ({seizedAt:0.000} m -> "
               + $"{cylinder.Extension:0.000} m)");
        Expect(!Bit("cylinder.extended") && !Bit("cylinder.retracted"),
               "with both reeds open, which is the only honest thing to read");
        Tags.ClearForce("cylinder.fault");
        Tags.Set("cylinder.extend", false);
    }

    // ---------- DosingPump and FlowMeter

    /// <summary>The pump really moves liquid: the tank's level rises with both
    /// of its own valves shut, so every litre came from the pump.</summary>
    private void CheckPumpFillsTheTank()
    {
        var tank = Part<LevelTank>("tank");

        Expect(Num("tank.fill") < 0.5 && Num("tank.drain") < 0.5,
               "the tank's own valves are shut, so nothing else can be filling it");

        float before = tank.Level;
        Tags.Set("pump.run", true);
        Tags.Set("pump.speed", 100.0);
        Run(60 * 10);

        // 60 L/min into a 100 L tank for 10 s is 10 L, which is 10 %.
        Expect(tank.Level > before + 5.0f,
               $"the pump fills the tank ({before:0.0} % -> {tank.Level:0.0} %)");
        Expect(Num("pump.flow") > 50.0,
               $"and reports the flow it is delivering ({Num("pump.flow"):0.0} L/min)");

        // A failed pump still reports its command and delivers nothing. Only
        // the measurement gives it away, which is the analog failure mode.
        Tags.Force("pump.fault", true);
        Run(30);
        float faultedAt = tank.Level;
        Run(60 * 5);
        Expect(Mathf.IsEqualApprox(tank.Level, faultedAt),
               $"a failed pump delivers nothing ({faultedAt:0.0} % -> {tank.Level:0.0} %)");
        Expect(Num("pump.flow") < 0.5,
               "and the flow measurement is what says so -- the command still reads 100 %");
        Expect(Num("pump.speed") > 99.0,
               "while the speed reference is unchanged, so the two genuinely disagree");
        Tags.ClearForce("pump.fault");
    }

    /// <summary>The meter reads the pump it is plumbed to, totalises it, and
    /// treats its reset as a level.</summary>
    private void CheckFlowMeterReadsAndTotalises()
    {
        Run(60 * 2);
        Expect(Num("meter.rate") > 50.0,
               $"the meter reads the flow the pump is delivering ({Num("meter.rate"):0.0} L/min)");

        // Zero the totaliser, then run a known time. 60 L/min for 10 s is 10 L.
        Tags.Set("meter.reset", true);
        Run(4);
        Expect(System.Convert.ToInt32(Tags.Visible("meter.total")) == 0,
               "holding the reset zeroes the totaliser");

        Tags.Set("meter.reset", false);
        Run(60 * 10);
        int total = System.Convert.ToInt32(Tags.Visible("meter.total"));
        Expect(total >= 8 && total <= 12,
               $"and it totalises what actually passed: 60 L/min for 10 s is 10 L, read {total} L");

        // A level, not an edge: held high, the total stays at zero rather than
        // resetting once and counting on.
        Tags.Set("meter.reset", true);
        Run(60 * 5);
        Expect(System.Convert.ToInt32(Tags.Visible("meter.total")) == 0,
               "a reset held high holds the total at zero -- it is a level, like a "
               + "counter's own reset, not an edge");

        Tags.Set("meter.reset", false);
        Tags.Set("pump.run", false);
        Run(60);
        Expect(Num("meter.rate") < 1.0,
               $"and the rate falls back to nothing when the pump stops ({Num("meter.rate"):0.0})");
    }
}
