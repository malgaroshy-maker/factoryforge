using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check that a drive can fail, and that failing means what it says
/// (FI-01):
///
/// <code>godot --headless --path engine -- --self-test=fault</code>
///
/// Every actuator in the library used to do exactly what it was told, so a
/// command and reality could never disagree. That made half of real PLC work
/// unteachable here — an interlock exists because the plant does not always
/// obey, and a student who has only driven a line that always obeys has never
/// had to check.
///
/// So the assertion that matters is not "the belt stopped". It is that the
/// belt stopped **while the command was still on**: a fault that also cleared
/// the command would leave the two agreeing, which is the one thing this
/// feature exists to prevent.
/// </summary>
public partial class FaultInjectionSelfTest : Node
{
    public SceneEditor Editor { get; set; } = null!;
    public TagTable Tags { get; set; } = null!;

    private readonly List<string> _failures = new();
    private int _step;
    private ConveyorBelt _belt = null!;
    private PusherMechanism _pusher = null!;
    private LevelTank _tank = null!;
    private WeighingConveyor _scale = null!;
    private float _extensionWhenFaulted;
    private float _levelWhenSeized;

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    private T? Find<T>() where T : Node3D
    {
        foreach (var child in GetParent().GetChildren())
        {
            if (child is T found) return found;
        }
        return null;
    }

    private bool Bit(string id) => Tags.Contains(id) && Tags.Visible(id) is true;

    /// <summary>A ray straight down onto a part, standing in for a click on it
    /// with the fault tool armed.</summary>
    private static (Vector3 From, Vector3 Dir) Over(Node3D node) =>
        (node.GlobalPosition + new Vector3(0, 6.0f, 0), Vector3.Down);

    public override void _PhysicsProcess(double delta)
    {
        _step++;

        switch (_step)
        {
            case 1:
                Editor.LoadTemplate("res://templates/light_curtain_sorting.json");
                return;

            case 3:
            {
                var belt = Find<ConveyorBelt>();
                var pusher = Find<PusherMechanism>();
                if (belt is null || pusher is null)
                {
                    Expect(false, "light-curtain-sorting: no belt or no pusher");
                    Finish();
                    return;
                }
                _belt = belt;
                _pusher = pusher;

                foreach (string id in new[] { "belt.fault", "diverter.fault" })
                    Expect(Tags.Contains(id), $"tag {id} exists");
                Expect(Tags.Get("belt.fault")?.Kind == TagKind.Input,
                       "belt.fault is an Input — nothing in the simulation computes it");
                Expect(!Bit("belt.fault"), "a fresh scene has no drive faulted");

                Tags.Set("belt.rotate", true);
                return;
            }

            case 5:
                Expect(_belt.IsRunning, "the belt runs when commanded");
                Tags.Force("belt.fault", true);
                return;

            case 7:
                Expect(!_belt.IsRunning, "a faulted drive stops");
                Expect(_belt.IsFaulted, "and knows it is faulted");
                // The whole point. A fault that also dropped the command would
                // leave the two agreeing and teach nothing.
                Expect(Bit("belt.rotate"),
                       "the command is still on — the drive is disobeying, not obeying a stop");
                Expect(_belt.ConstantLinearVelocity.Length() < 0.001f,
                       $"the belt surface is actually still (v={_belt.ConstantLinearVelocity.Length():0.###})");
                return;

            case 9:
                // A command arriving *while* faulted must not start it either.
                Tags.Set("belt.rotate", false);
                return;

            case 11:
                Tags.Set("belt.rotate", true);
                return;

            case 13:
                Expect(!_belt.IsRunning, "commanding a faulted drive on again does not start it");
                Tags.ClearForce("belt.fault");
                return;

            case 15:
                Expect(_belt.IsRunning, "clearing the fault lets the standing command take effect");
                Expect(!_belt.IsFaulted, "and the drive stops reporting a fault");

                // Now the pusher: a jammed cylinder stops where it is. Not
                // "returns home" -- a stuck actuator is dangerous precisely
                // because it does not go anywhere safe on its own.
                Tags.Set("diverter.extend", true);
                return;

            case 30:
                Expect(_pusher.Extension > 0.01f,
                       $"the pusher is mid-stroke before the jam (at {_pusher.Extension:0.###})");
                _extensionWhenFaulted = _pusher.Extension;
                Tags.Force("diverter.fault", true);
                return;

            case 50:
                Expect(Mathf.Abs(_pusher.Extension - _extensionWhenFaulted) < 0.001f,
                       $"a jammed cylinder stops where it is (was {_extensionWhenFaulted:0.###}, "
                       + $"now {_pusher.Extension:0.###})");
                Tags.Set("diverter.extend", false);
                return;

            case 70:
                Expect(Mathf.Abs(_pusher.Extension - _extensionWhenFaulted) < 0.001f,
                       "a jammed cylinder does not retract on command either — it is stuck, "
                       + "which is why the limit switches are the thing to read");
                Expect(!_pusher.IsRetracted,
                       "and its retracted limit is honest about that");
                Tags.ClearForce("diverter.fault");
                return;

            case 90:
                Expect(_pusher.IsRetracted,
                       $"clearing the jam lets it finish the move (at {_pusher.Extension:0.###})");
                CheckToolTargets();
                Editor.SetMode(EditorMode.Edit);
                Editor.LoadTemplate("res://templates/tank_level_control.json");
                return;

            // --- the analog failure -------------------------------------
            case 92:
            {
                var tank = Find<LevelTank>();
                if (tank is null) { Expect(false, "tank-level-control: no tank"); Finish(); return; }
                _tank = tank;
                Expect(Tags.Contains("tank.fault"), "tag tank.fault exists");
                Expect(Editor.CanFault("LevelTank"), "a tank's valves can be seized");
                Tags.Set("tank.fill", 60.0);
                return;
            }

            case 120:
                Expect(_tank.Level > 1.0f, $"the tank is filling ({_tank.Level:0.#}%)");
                _levelWhenSeized = _tank.Level;
                Tags.Force("tank.fault", true);
                Tags.Set("tank.fill", 0.0);
                return;

            case 150:
                // A seized valve holds its opening, so the tank keeps filling
                // while the command reads zero. That is the whole difference
                // between this and a stopped drive: the process keeps moving
                // and the controller's own output cannot tell you.
                Expect(_tank.Level > _levelWhenSeized + 1.0f,
                       $"a seized valve keeps filling while commanded shut "
                       + $"({_levelWhenSeized:0.#}% -> {_tank.Level:0.#}%)");
                Expect(System.Convert.ToDouble(Tags.Visible("tank.fill")) < 0.5,
                       "and the command really does read zero — the two disagree");
                _levelWhenSeized = _tank.Level;
                Tags.ClearForce("tank.fault");
                return;

            case 180:
                Expect(Mathf.Abs(_tank.Level - _levelWhenSeized) < 0.5f,
                       $"freeing the valve lets the standing shut command take effect "
                       + $"({_levelWhenSeized:0.#}% -> {_tank.Level:0.#}%)");
                Editor.SetMode(EditorMode.Edit);
                Editor.LoadTemplate("res://templates/roller_line_weighing.json");
                return;

            // --- the fault contact that did nothing (HP-35) ---------------
            case 182:
            {
                var scale = Find<WeighingConveyor>();
                if (scale is null) { Expect(false, "roller-line-weighing: no weigh deck"); Finish(); return; }
                _scale = scale;

                // The tag has existed all along -- PartTagManager registers
                // <id>.fault for a WeighingConveyor exactly as it does for every
                // other conveyor, so it appears in the inspector and can be
                // forced. Nothing dispatched it, so forcing it did nothing at
                // all: an advertised contact wired to no effect.
                Expect(Tags.Contains("scale.fault"), "tag scale.fault exists");
                Expect(Editor.CanFault("WeighingConveyor"), "and the tool offers to use it");

                Tags.Set("scale.rotate", true);
                return;
            }

            case 184:
                Expect(_scale.IsRunning, "the weigh deck runs when commanded");
                Tags.Force("scale.fault", true);
                return;

            case 186:
                Expect(!_scale.IsRunning, "a faulted weigh deck stops");
                Expect(_scale.IsFaulted, "and knows it is faulted");
                // Asserting the effect, not the flag: SetFaulted is inherited
                // from ConveyorBelt and was simply never called for this
                // subclass, so a check on IsFaulted alone would have passed the
                // moment the call appeared, whatever it did.
                Expect(_scale.ConstantLinearVelocity.Length() < 0.001f,
                       $"and the deck surface is actually still "
                       + $"(v={_scale.ConstantLinearVelocity.Length():0.###})");
                Expect(Bit("scale.rotate"),
                       "with the command still on — the drive is disobeying, as every other conveyor does");
                Tags.ClearForce("scale.fault");
                return;

            case 188:
                Expect(_scale.IsRunning, "clearing it lets the standing command take effect again");
                Finish();
                return;
        }
    }

    /// <summary>The fault tool aims at drives and nothing else. A tool that
    /// silently did nothing on half the parts would be worse than one that
    /// says so, and a tool that faulted a stack light would be nonsense.
    /// </summary>
    private void CheckToolTargets()
    {
        Expect(Editor.CanFault("ConveyorBelt"), "a conveyor can be faulted");
        Expect(Editor.CanFault("RollerConveyor"), "a roller deck can be faulted");
        Expect(Editor.CanFault("WeighingConveyor"), "a weigh deck can be faulted");
        Expect(Editor.CanFault("PusherMechanism"), "a pusher can be faulted");
        Expect(Editor.CanFault("LevelTank"), "a tank's valves can be seized");
        Expect(!Editor.CanFault("StackLight"), "a stack light has no drive to fail");
        Expect(!Editor.CanFault("ButtonPanel"), "a control panel has no drive to fail");
        Expect(!Editor.CanFault("PhotoelectricSensor"), "a sensor has no drive to fail");

        // Arming is Run-mode only: a fault tool live while you are dragging
        // conveyors into place would be a trap.
        Editor.SetMode(EditorMode.Edit);
        Editor.SetFaultToolArmed(true);
        Expect(!Editor.FaultToolArmed, "the fault tool refuses to arm in Build mode");

        var (from, dir) = Over(_belt);
        Expect(Editor.ToggleFaultAtRay(from, dir) is null,
               "and refuses to fault anything there either");

        Editor.SetMode(EditorMode.Run);
        Editor.SetFaultToolArmed(true);
        Expect(Editor.FaultToolArmed, "it arms in Operate mode");

        Expect(Editor.ToggleFaultAtRay(from, dir) == "belt",
               "a click on the belt faults the belt, by name");
        Expect(Bit("belt.fault"), "and the tag says so");
        Expect(Tags.IsForced("belt.fault"),
               "held as a force, so the Tag Inspector shows it held and one click releases it");
        Expect(Editor.ToggleFaultAtRay(from, dir) == "belt", "a second click targets it again");
        Expect(!Bit("belt.fault"), "and clears the fault");

        // The outline has to promise what the click will actually do. Armed, it
        // lands on drives; disarmed, on operable parts. An outline over a part
        // the armed click is about to break -- shown in the colour that means
        // "this operates it" -- is the same class of lie as an outline over a
        // part a click would miss.
        Editor.SetFaultToolArmed(true);
        Expect(Editor.HoverTargetAtRay(from, dir) == "belt",
               $"armed: hovering the belt outlines the belt "
               + $"(got '{Editor.HoverTargetAtRay(from, dir)}')");

        // An emitter is the useful case: operable, but with no drive to fail.
        // Armed, the outline must not land on it — outlining a part the click
        // is about to *not* break is the lie being prevented here.
        var emitter = Find<Emitter>();
        if (emitter is not null)
        {
            var (ef, ed) = Over(emitter);
            Expect(Editor.HoverTargetAtRay(ef, ed) is not "emitter",
                   "armed: an emitter is not a fault target — it has no drive");
            Editor.SetFaultToolArmed(false);
            Expect(Editor.HoverTargetAtRay(ef, ed) == "emitter",
                   $"disarmed: the same emitter is an operable target "
                   + $"(got '{Editor.HoverTargetAtRay(ef, ed)}')");
            Editor.SetFaultToolArmed(true);
        }

        Editor.SetFaultToolArmed(false);
        Expect(!Editor.FaultToolArmed, "and it disarms");
    }

    private void Finish()
    {
        if (_failures.Count == 0)
        {
            GD.Print("self-test fault: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test fault: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }
}
