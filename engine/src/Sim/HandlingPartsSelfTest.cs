using System.Collections.Generic;
using FactoryForge.Editor;
using FactoryForge.Parts;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Headless check that the three handling parts added in HA-01…HA-03 do what
/// their tags claim:
///
/// <code>godot --headless --path engine -- --self-test=handlingparts</code>
///
/// Same rule as <see cref="LinePartsSelfTest"/> and
/// <see cref="ControlPartsSelfTest"/>: assert the <em>effect</em>. "The tag
/// exists" is true of any part the moment it is placed and has never once
/// caught anything.
///
/// So what is asserted here is that a jointed arm refuses a command past a
/// mechanical stop and does not reach any further for being asked; that moving
/// two joints at once puts the tool on a curve and not on the straight line
/// between where it started and where it finished; that a degree of elbow is
/// worth two orders of magnitude less reach at full stretch than it is at 90°,
/// which is what a singularity <em>is</em>; that a pallet station's pattern
/// walks a row, wraps to the next, rises a layer, turns a quarter-turn on the
/// odd ones, and then refuses outright rather than stacking into the air; and
/// that a lift's carriage takes exactly one carton, physically holds the next
/// one on the belt outside while it is occupied, carries its load up a level
/// for real, and lets the queue go when it comes back empty.
///
/// The test is phased. Four of those claims are claims about the solver — a
/// carton riding a carriage, a second carton held by a blade, a gripper closing
/// on a rigid body, a pallet change clearing what is standing on it — and none
/// of them can be reached by turning the dispatch by hand, because the
/// <c>Area3D</c>s and the contacts belong to the physics server. Those run on
/// real engine ticks. Everything that is arithmetic runs hand-turned at the
/// end, where a four-hundred-tick joint move costs no wall time.
/// </summary>
public partial class HandlingPartsSelfTest : Node
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

    /// <summary>Turn the dispatch by hand. Free, but it moves the dispatch and
    /// not the world, so anything that depends on the physics server has to use
    /// real ticks instead.</summary>
    private void Run(int ticks)
    {
        for (int i = 0; i < ticks; i++) Editor._PhysicsProcess(Tick);
    }

    private double Num(string id) => System.Convert.ToDouble(Tags.Visible(id));
    private int Whole(string id) => System.Convert.ToInt32(Tags.Visible(id));
    private bool Bit(string id) => Tags.Contains(id) && Tags.Visible(id) is true;

    private T Part<T>(string id) where T : Node3D
    {
        var node = Editor.NodeFor(id) as T;
        if (node is null) throw new System.InvalidOperationException($"'{id}' is not a {typeof(T).Name}");
        return node;
    }

    // --- the arm's geometry, as the scene builds it. Written once here so the
    // reach arithmetic below is checked against the machine rather than against
    // a number typed twice.
    private const float UpperArm = 0.60f;
    private const float Forearm = 0.40f;
    private const float MaxReach = UpperArm + Forearm;

    private const float LiftSpacing = 0.90f;

    // --- physical probes, carried across the real-tick phases
    private BoxPhysics? _onCarriage;
    private BoxPhysics? _queued;
    private BoxPhysics? _gripped;
    private BoxPhysics? _onPallet;
    private float _carriageCartonStartY;

    /// <summary>The carriage deck is running to draw the queued carton aboard,
    /// and has to stop the moment it is. A real carriage does exactly this, and
    /// the alternative -- a fixed number of ticks -- would be a test tuned to
    /// one belt speed rather than one watching the machine.</summary>
    private bool _drawingAboard;

    public override void _PhysicsProcess(double delta)
    {
        _step++;

        // A driven carriage deck stops as soon as it has its load, or the load
        // rides straight off the far side.
        if (_drawingAboard && Bit("lift.occupied"))
        {
            Tags.Set("lift.transfer", false);
            _drawingAboard = false;
        }

        switch (_step)
        {
            // Two ticks of grace: parts build their geometry in _Ready, which
            // does not run until the node has been in the tree for a frame, and
            // most of what is asserted below reads that geometry back.
            case 2: BuildScene(); return;
            case 4: CheckGuardsAgainstImpossibleGeometry(); return;

            case 100: StartLiftProbe(); return;
            case 145: CheckTheCarriageTookIt(); return;
            case 240: CheckTheGateShutBehindIt(); return;
            case 420: CheckTheSecondCartonIsHeldOutside(); return;
            case 520: CheckItReallyLifted(); return;
            case 600: CheckTheDeckDischarges(); return;
            case 700: DrawTheQueuedCartonAboard(); return;
            case 900: CheckTheQueueIsReleased(); return;

            case 910: StartGripperProbe(); return;
            case 918: CheckTheArmPickedItUp(); return;
            case 990: CheckTheArmCarriedIt(); return;
            case 1000: CheckTheArmLetGo(); StartPalletChangeProbe(); return;
            case 1060: CheckThePalletChangeClearedTheStack(); return;

            case 1062: break;
            default: return;
        }

        if (_done) return;
        _done = true;

        try
        {
            CheckArmRefusesPastItsStops();
            CheckArmDoesNotMoveInStraightLines();
            CheckArmLosesAuthorityAtFullStretch();
            CheckArmSeizesWhereItStands();

            CheckPalletPatternWalksAndWraps();
            CheckPalletLayersRiseAndInterlock();
            CheckPalletFillsAndRefuses();
            CheckPalletIndexIsAnEdge();

            CheckLiftClampsAnImpossibleCall();
            CheckLiftSeizesBetweenFloors();
        }
        catch (System.Exception ex)
        {
            // Gotcha 12: a throw here is logged by Godot and the run continues
            // to its --duration, so without this the report never happens and
            // the exit code is success.
            _failures.Add(ex.Message);
            GD.PrintErr($"  FAIL  threw: {ex.GetType().Name}: {ex.Message}");
        }

        // Gotcha 21: the side effect that ends the run goes first. Everything
        // above has already happened; nothing below it may throw.
        if (_failures.Count == 0)
        {
            GD.Print("self-test handlingparts: PASS");
            GetTree().Quit(0);
        }
        else
        {
            GD.PrintErr($"self-test handlingparts: FAIL ({_failures.Count})");
            GetTree().Quit(1);
        }
    }

    // ---------- the scene

    private static PartInstanceData At(string id, string type, float x, float z,
                                       Dictionary<string, string>? props = null) =>
        new()
        {
            Id = id,
            Type = type,
            Position = new[] { x, PartLayout.WorkPlaneY, z },
            Rotation = new[] { 0.0f, 0.0f, 0.0f },
            Properties = props,
        };

    private void BuildScene()
    {
        var data = new SceneData { Name = "selftest-handling-parts" };

        // --- the lift, with a feed belt running up to its gate. The belt's
        // head end and the carriage deck very nearly touch, which is what the
        // transfer wedges on both of them are for (gotcha 23).
        data.Parts.Add(At("lift", "VerticalLift", 0.0f, 0.0f, new Dictionary<string, string>
        {
            ["levels"] = "3",
            ["spacing"] = "0.9",
            ["hoist_speed"] = "0.75",
            ["infeed_level"] = "0",
            ["transfer_speed"] = "0.5",
            ["tolerance"] = "0.02",
        }));
        data.Parts.Add(At("feed", "ConveyorBelt", -1.0f, 0.0f, new Dictionary<string, string>
        {
            ["size_x"] = "1.5", ["size_y"] = "0.12", ["size_z"] = "0.5", ["speed"] = "0.5",
        }));

        // --- the arm, with a table under its working radius to stand a carton
        // on. Headless has no floor, so a carton with nothing under it falls
        // for ever and every assertion about it becomes a statement about
        // gravity.
        data.Parts.Add(At("arm", "ArticulatedArm", 8.0f, 0.0f, new Dictionary<string, string>
        {
            ["upper_arm"] = "0.60",
            ["forearm"] = "0.40",
            ["shoulder_height"] = "0.78",
            ["joint_speed"] = "60",
            ["tolerance"] = "0.5",
            ["waist_limit"] = "165",
            ["shoulder_min"] = "-35",
            ["shoulder_max"] = "105",
            ["elbow_max"] = "150",
        }));
        data.Parts.Add(At("armtable", "ConveyorBelt", 8.0f + UpperArm, 0.0f,
                          new Dictionary<string, string>
                          {
                              ["size_x"] = "1.5", ["size_y"] = "0.12", ["size_z"] = "0.5",
                              ["speed"] = "0",
                          }));

        data.Parts.Add(At("pallet", "PalletStation", 16.0f, 0.0f, new Dictionary<string, string>
        {
            ["columns"] = "3",
            ["rows"] = "2",
            ["layers"] = "2",
            ["pitch_x"] = "0.24",
            ["pitch_z"] = "0.28",
            ["layer_height"] = "0.12",
            ["alternate"] = "true",
        }));

        // --- two parts built from geometry nobody could mean, well clear of
        // everything else. A hand-edited scene file is the realistic source of
        // both, and a non-finite float now throws at TagTable.Set (HP-23), so
        // "the tick survived" is a real assertion and not a formality.
        data.Parts.Add(At("armzero", "ArticulatedArm", 8.0f, 6.0f,
                          new Dictionary<string, string>
                          {
                              ["upper_arm"] = "0", ["forearm"] = "0",
                          }));
        data.Parts.Add(At("palletzero", "PalletStation", 16.0f, 6.0f,
                          new Dictionary<string, string>
                          {
                              ["columns"] = "0", ["rows"] = "0", ["layers"] = "0",
                          }));

        const string path = "user://selftest_handlingparts.json";
        using (var file = Godot.FileAccess.Open(path, Godot.FileAccess.ModeFlags.Write))
        {
            file?.StoreString(data.ToJson());
        }
        Editor.LoadSceneFromFile(path);
    }

    // ---------- HP-23: geometry nobody could mean

    /// <summary>
    /// A zero-length link and a pattern with zero columns.
    ///
    /// These are the two divisions in this batch of parts — reach over maximum
    /// reach, and a slot index modulo the column count — and both are reachable
    /// from a hand-edited scene file. One would put a NaN on the wire, which
    /// <c>TagTable.Set</c> now refuses; the other is an <em>integer</em> modulo
    /// by zero, which throws outright and takes the whole tick dispatch with it
    /// on every frame for the rest of the run. Both are clamped where the value
    /// arrives rather than guarded at each use.
    /// </summary>
    private void CheckGuardsAgainstImpossibleGeometry()
    {
        var arm = Part<ArticulatedArm>("armzero");
        Expect(arm.MaxReach > 0.0f,
               $"an arm loaded with zero-length links still has a non-zero maximum reach "
               + $"({arm.MaxReach:0.000} m), so `stretch` is a division and not a NaN");
        Expect(float.IsFinite(arm.Stretch), $"and its stretch is a real number ({arm.Stretch})");
        Expect(float.IsFinite((float)Num("armzero.stretch")),
               "and the number that reached the tag bus is finite");
        // And the tick that published it ran to the end. A NaN throws at
        // TagTable.Set, Godot logs it and carries on (gotcha 12), and every tag
        // the part writes *after* the offending one silently keeps the value it
        // powered up with -- which reads as a pass. `limit` is written after
        // `stretch`, so commanding something impossible is what proves the
        // whole tick happened.
        Tags.Set("armzero.elbow", -50.0);
        Run(2);
        Expect(Bit("armzero.limit"),
               "and the tick got past it: `limit` is published after `stretch`, so an "
               + "impossible command showing up there means nothing threw on the way");
        Tags.Set("armzero.elbow", 90.0);

        var pallet = Part<PalletStation>("palletzero");
        Expect(pallet.SlotsPerLayer >= 1,
               $"a pattern loaded with zero columns and zero rows still has at least one slot "
               + $"({pallet.SlotsPerLayer}) — `slot % columns` is an integer modulo, which "
               + $"throws rather than returning a NaN");
        Expect(pallet.Capacity >= 1, $"and a capacity of at least one ({pallet.Capacity})");
        Expect(!Bit("palletzero.full"),
               "and an empty pallet does not report itself full, which a zero capacity would");
        Expect(float.IsFinite((float)Num("palletzero.nextx")),
               "and it publishes a real position for the next carton");
        // Same trap as the arm above, and worse: `slot % columns` is an
        // *integer* modulo, so zero columns throws outright before a single tag
        // of that part's tick is written, and every one of them keeps its
        // power-up value. `count` moving is what proves the tick ran.
        Tags.Set("palletzero.index", true);
        Run(2);
        Tags.Set("palletzero.index", false);
        Run(2);
        Expect(Whole("palletzero.count") == 1,
               $"and its tick ran to the end — one index moved the count to 1, not "
               + $"{Whole("palletzero.count")}, which is what it would still read if the "
               + $"modulo had thrown before anything was published");
    }

    // ---------- HA-03: the lift, on real physics

    private BoxPhysics Spawn(string name, Vector3 where)
    {
        var box = new BoxPhysics { Name = name, IsTall = false };
        Editor.GetParent().AddChild(box);
        box.GlobalPosition = where;
        return box;
    }

    private void StartLiftProbe()
    {
        var lift = Part<VerticalLift>("lift");

        Expect(Bit("lift.ready"),
               "a lift parked at its infeed level with an empty carriage opens its gate and "
               + "says it will take one");
        Expect(lift.IsGateOpen, "and the blade is actually down, not merely reported down");
        Expect(!Bit("lift.occupied"), "with nothing aboard");

        // Stand a carton on the carriage. Dropped from just above the deck
        // rather than teleported onto it, so it settles into a real contact.
        _onCarriage = Spawn("LiftCartonA",
                            new Vector3(0.0f, PartLayout.WorkPlaneY + 0.26f, 0.0f));
    }

    private void CheckTheCarriageTookIt()
    {
        var box = _onCarriage!;
        Expect(GodotObject.IsInstanceValid(box), "the carton on the carriage still exists");
        Expect(Bit("lift.occupied"),
               $"a carton standing on the deck is seen as occupying the carriage "
               + $"(carton at y={box.GlobalPosition.Y:0.000})");
        Expect(!Bit("lift.ready"),
               "and the lift stops saying it will take one the moment it has one");

        _carriageCartonStartY = box.GlobalPosition.Y;

        // Send the next carton up the feed belt. It has about a metre to run.
        Tags.Set("feed.rotate", true);
        _queued = Spawn("LiftCartonB",
                        new Vector3(-1.40f, PartLayout.WorkPlaneY + 0.14f, 0.0f));
    }

    private void CheckTheGateShutBehindIt()
    {
        var lift = Part<VerticalLift>("lift");
        Expect(!lift.IsGateOpen,
               "the entry blade rises once the carriage is occupied — nothing in the "
               + "controller asked it to, because a resource that relies on being asked is "
               + "not interlocked");
    }

    /// <summary>
    /// The assertion this part exists for: a second carton is <em>physically</em>
    /// refused.
    ///
    /// Not "the controller was told not to send one" — a blade across the mouth,
    /// a carton stopped against it on a belt that is still running, and a
    /// carriage that still has exactly one thing on it.
    /// </summary>
    private void CheckTheSecondCartonIsHeldOutside()
    {
        var box = _queued!;
        Expect(GodotObject.IsInstanceValid(box), "the second carton still exists");
        if (!GodotObject.IsInstanceValid(box)) return;

        float x = box.GlobalPosition.X;
        Expect(x > -0.80f,
               $"the second carton travelled up the running belt (x={x:0.000})");
        Expect(x < -0.30f,
               $"and is held outside the shaft by the entry blade rather than riding onto an "
               + $"occupied carriage (x={x:0.000}, deck starts at -0.230)");
        Expect(Mathf.Abs(box.GlobalPosition.Y - PartLayout.WorkPlaneY) < 0.25f,
               $"still on the belt, not on top of the first carton (y={box.GlobalPosition.Y:0.000})");

        Expect(Bit("lift.occupied"), "and the carriage still holds exactly the first one");

        // Now take that one up a level.
        Tags.Set("lift.target", 1);
    }

    private void CheckItReallyLifted()
    {
        var lift = Part<VerticalLift>("lift");
        var box = _onCarriage!;

        Expect(Bit("lift.atlevel"), "the carriage reaches the level it was called to");
        Expect(Whole("lift.level") == 1, $"and reports it ({Whole("lift.level")})");
        Expect(Mathf.Abs((float)Num("lift.height") - LiftSpacing) < 0.05f,
               $"at the height that level is at ({Num("lift.height"):0.000} m)");

        Expect(GodotObject.IsInstanceValid(box), "the carton is still there");
        if (!GodotObject.IsInstanceValid(box)) return;

        float rise = box.GlobalPosition.Y - _carriageCartonStartY;
        // The load is carried by contact, not by being parented to the deck, so
        // this is the whole claim: the carriage came up underneath it.
        Expect(rise > LiftSpacing - 0.08f,
               $"and it rode up with the carriage rather than being left behind "
               + $"(rose {rise:0.000} m of {LiftSpacing:0.00})");
        Expect(Bit("lift.occupied"), "and it is still aboard at the top");
        Expect(!Bit("lift.ready"),
               "and the lift does not claim to be ready — it is neither at the infeed level "
               + "nor empty");

        // Discharge it at the upper level.
        Tags.Set("lift.transfer", true);
    }

    private void CheckTheDeckDischarges()
    {
        var box = _onCarriage;

        Expect(!Bit("lift.occupied"),
               "running the deck transfer takes the carton off the carriage — a lift that "
               + "cannot discharge is a shelf");
        if (box is not null && GodotObject.IsInstanceValid(box))
        {
            Expect(box.GlobalPosition.X > 0.20f,
                   $"and it left forwards, driven by the deck rather than dropped "
                   + $"(x={box.GlobalPosition.X:0.000})");
        }

        var lift = Part<VerticalLift>("lift");
        Expect(!lift.IsGateOpen,
               "an empty carriage at the wrong level still keeps its gate shut — being empty "
               + "is not being available");

        Tags.Set("lift.transfer", false);
        Tags.Set("lift.target", 0);
    }

    /// <summary>Start the carriage deck. A lift accepts product by running its
    /// own deck; the belt outside can only bring a carton to the mouth, which
    /// is why <c>transfer</c> exists and why a carton left at the threshold of
    /// an undriven deck simply stops there.</summary>
    private void DrawTheQueuedCartonAboard()
    {
        Tags.Set("lift.transfer", true);
        _drawingAboard = true;
    }

    private void CheckTheQueueIsReleased()
    {
        Expect(!Bit("lift.transfer"),
               "the carriage stopped its own deck once it had the carton — the test asked "
               + "for it to stop on `occupied`, so this failing means occupancy never came");

        Expect(Bit("lift.occupied"),
               "bringing the empty carriage back to the infeed level opens the gate and the "
               + "queued carton takes itself aboard — the interlock released without anybody "
               + "writing to it");

        var box = _queued;
        if (box is not null && GodotObject.IsInstanceValid(box))
        {
            Expect(Mathf.Abs(box.GlobalPosition.X) < 0.24f,
                   $"and it is the queued carton that is on the deck (x={box.GlobalPosition.X:0.000})");
        }
    }

    // ---------- HA-01: the gripper, on real physics

    /// <summary>
    /// Put a carton where the tool actually is and close the jaws.
    ///
    /// The pose is read off the machine rather than computed here on purpose: a
    /// second copy of the kinematics in the test would agree with a wrong first
    /// copy in the part, and the two of them would pass together.
    /// </summary>
    private void StartGripperProbe()
    {
        var arm = Part<ArticulatedArm>("arm");

        _gripped = Spawn("ArmCarton", arm.GlobalTransform * arm.ToolLocal - new Vector3(0, 0.06f, 0));
        Tags.Set("arm.grip", true);
    }

    private void CheckTheArmPickedItUp()
    {
        Expect(Bit("arm.holding"),
               "closing the jaws on a carton in reach picks it up");
        Expect(GodotObject.IsInstanceValid(_gripped!), "and the carton is still a carton");

        // Swing it. A carried load has to follow the tool through a joint move,
        // which is the difference between holding something and having reported
        // that you did.
        Tags.Set("arm.waist", 60.0);
    }

    private void CheckTheArmCarriedIt()
    {
        var arm = Part<ArticulatedArm>("arm");
        var box = _gripped!;

        Expect(arm.WaistAngle > 50.0f,
               $"the waist swung round with a carton in the jaws ({arm.WaistAngle:0.0}°)");
        Expect(GodotObject.IsInstanceValid(box), "the carried carton still exists");
        if (!GodotObject.IsInstanceValid(box)) return;

        Vector3 tool = arm.GlobalTransform * arm.ToolLocal;
        Expect(box.GlobalPosition.DistanceTo(tool) < 0.16f,
               $"and the carton went with the tool rather than staying where it was "
               + $"(carton {box.GlobalPosition}, tool {tool})");

        Tags.Set("arm.grip", false);
    }

    private void CheckTheArmLetGo()
    {
        Expect(!Bit("arm.holding"), "opening the jaws lets the carton go");
    }

    // ---------- HA-02: a pallet change, on real physics

    private void StartPalletChangeProbe()
    {
        var pallet = Part<PalletStation>("pallet");
        _onPallet = Spawn("PalletCarton",
                          pallet.GlobalPosition + new Vector3(0.0f, 0.30f, 0.0f));
    }

    private void CheckThePalletChangeClearedTheStack()
    {
        var box = _onPallet!;
        Expect(GodotObject.IsInstanceValid(box) && !box.IsQueuedForDeletion(),
               "a carton dropped on the pallet is standing on it");
        Expect(GodotObject.IsInstanceValid(box)
               && box.GlobalPosition.Y > PartLayout.WorkPlaneY,
               $"at pallet-deck height rather than falling past it "
               + $"(y={(GodotObject.IsInstanceValid(box) ? box.GlobalPosition.Y : float.NaN):0.000})");

        // The full pallet drives away. Its load goes with it.
        Tags.Set("pallet.change", true);
        Run(2);
        Tags.Set("pallet.change", false);
        Run(2);

        Expect(!GodotObject.IsInstanceValid(box) || box.IsQueuedForDeletion(),
               "and a pallet change takes it with it — a change that left the load behind "
               + "would build the next layer inside the last one");
    }

    // ---------- HA-01: what the gantry cannot teach

    private void PoseArm(float waist, float shoulder, float elbow, int settleTicks = 400)
    {
        Tags.Set("arm.waist", (double)waist);
        Tags.Set("arm.shoulder", (double)shoulder);
        Tags.Set("arm.elbow", (double)elbow);
        Run(settleTicks);
    }

    /// <summary>
    /// A command past a mechanical stop is clamped and said so, and the tool
    /// does not get any further out for the asking.
    ///
    /// This is the lesson the gantry structurally cannot teach. Every position a
    /// travel axis can be commanded to is a position it can reach; an arm has a
    /// boundary, and asking to cross it is not an error the machine reports
    /// later — it is a command the machine refuses now, which is why
    /// <c>limit</c> is about the command rather than about the position.
    /// </summary>
    private void CheckArmRefusesPastItsStops()
    {
        var arm = Part<ArticulatedArm>("arm");

        PoseArm(0, 0, 0);
        Expect(Mathf.Abs((float)Num("arm.reach") - MaxReach) < 0.005f,
               $"a straight arm reaches exactly the sum of its links "
               + $"({Num("arm.reach"):0.000} m of {MaxReach:0.000})");
        Expect(Num("arm.stretch") > 99.5,
               $"which is 100 % of its stretch ({Num("arm.stretch"):0.0} %)");
        Expect(!Bit("arm.limit"), "and nothing was clamped to get there");

        // Ask for more reach by bending the elbow backwards. There is no more.
        Tags.Set("arm.elbow", -40.0);
        Run(2);
        Expect(Bit("arm.limit"),
               "a command past the elbow stop raises `limit` on the scan it is written, "
               + "before the axis has had a chance to go anywhere");

        Run(400);
        Expect(Mathf.Abs(arm.ElbowAngle) < 0.01f,
               $"and the elbow stays on its stop ({arm.ElbowAngle:0.000}°)");
        Expect(Num("arm.reach") <= MaxReach + 0.005,
               $"so the tool is no further out than the links allow "
               + $"({Num("arm.reach"):0.000} m of {MaxReach:0.000})");
        Expect(Bit("arm.inposition"),
               "and the axis reports in position — against the clamped command, which is "
               + "where the machine actually is, not against the impossible one");

        // The shoulder's stop, the other way up.
        Tags.Set("arm.shoulder", 200.0);
        Run(400);
        Expect(Mathf.Abs(arm.ShoulderAngle - 105.0f) < 0.01f,
               $"the shoulder stops on its own limit too ({arm.ShoulderAngle:0.0}° of 105°)");

        // And a command back inside the envelope clears it.
        Tags.Set("arm.shoulder", 45.0);
        Tags.Set("arm.elbow", 30.0);
        Run(2);
        Expect(!Bit("arm.limit"),
               "a command back inside the envelope clears `limit` — it describes this scan's "
               + "command, not a latched history");
    }

    /// <summary>
    /// Two joints moving at once put the tool on an arc.
    ///
    /// The single most expensive thing to discover on a real arm, and the one
    /// the gantry can never show: on a gantry, commanding X and Y together does
    /// move the tool along the diagonal between them. Here the tool bows a long
    /// way off it, which is why a robot has a linear-interpolation mode at all
    /// and why a program written as if it did not is a program that swings
    /// through whatever is in the middle.
    /// </summary>
    private void CheckArmDoesNotMoveInStraightLines()
    {
        var arm = Part<ArticulatedArm>("arm");

        // Both endpoints first, because the chord is only known once the move
        // has finished — then the same move again, sampled against it.
        PoseArm(0, 0, 0);
        Vector3 from = arm.ToolLocal;
        PoseArm(0, 90, 90);
        Vector3 to = arm.ToolLocal;

        Expect(from.DistanceTo(to) > 0.30f,
               $"the tool genuinely moved between the two poses ({from.DistanceTo(to):0.000} m "
               + $"from {from} to {to})");

        PoseArm(0, 0, 0);
        Tags.Set("arm.shoulder", 90.0);
        Tags.Set("arm.elbow", 90.0);

        float worst = 0.0f;
        Vector3 worstAt = from;
        for (int i = 0; i < 400; i++)
        {
            Run(1);
            float off = PerpendicularDistance(from, to, arm.ToolLocal);
            if (off <= worst) continue;
            worst = off;
            worstAt = arm.ToolLocal;
        }

        Expect(worst > 0.05f,
               $"and it did not travel in a straight line to get there — the tool bowed "
               + $"{worst * 1000.0f:0} mm off the chord at {worstAt}, on a move where both "
               + $"joints ran at the same rate and arrived together");
    }

    /// <summary>
    /// At full stretch a degree of elbow is worth almost no reach at all.
    ///
    /// That is what a singularity is, stated in the only terms that matter to
    /// somebody writing the program: out there the arm cannot position itself
    /// radially, so a controller trying to hold a radius is chasing a number it
    /// has no authority over, and the same command that moves the tool 7 mm in
    /// the middle of the envelope moves it a twentieth of a millimetre at the
    /// edge.
    /// </summary>
    private void CheckArmLosesAuthorityAtFullStretch()
    {
        PoseArm(0, 0, 90);
        float atNinety = (float)Num("arm.reach");
        PoseArm(0, 0, 89);
        float nearNinety = Mathf.Abs((float)Num("arm.reach") - atNinety);

        PoseArm(0, 0, 0);
        float straight = (float)Num("arm.reach");
        PoseArm(0, 0, 1);
        float nearStraight = Mathf.Abs((float)Num("arm.reach") - straight);

        Expect(nearNinety > 0.004f,
               $"one degree of elbow at a right angle is worth real radial motion "
               + $"({nearNinety * 1000.0f:0.0} mm)");
        Expect(nearStraight < 0.0005f,
               $"and the same degree at full stretch is worth almost none "
               + $"({nearStraight * 1000.0f:0.000} mm)");
        Expect(nearNinety > nearStraight * 20.0f,
               $"— a factor of {(nearStraight > 0.0f ? nearNinety / nearStraight : float.PositiveInfinity):0}, "
               + $"which is the arm losing radial authority as the elbow straightens, and "
               + $"the reason `stretch` is worth publishing at all");
    }

    /// <summary>A seized arm stops where it stands, with the tool somewhere no
    /// limit switch describes. "Returns home" would be a safe failure, and safe
    /// failures teach nothing.</summary>
    private void CheckArmSeizesWhereItStands()
    {
        var arm = Part<ArticulatedArm>("arm");

        PoseArm(0, 0, 140);
        Tags.Set("arm.shoulder", 90.0);
        Tags.Set("arm.elbow", 10.0);
        Run(20);

        Tags.Force("arm.fault", true);
        float shoulder = arm.ShoulderAngle;
        float elbow = arm.ElbowAngle;
        Run(400);

        Expect(Mathf.IsEqualApprox(arm.ShoulderAngle, shoulder)
               && Mathf.IsEqualApprox(arm.ElbowAngle, elbow),
               $"a seized arm stops mid-move and stays there "
               + $"(shoulder {shoulder:0.0}° -> {arm.ShoulderAngle:0.0}°, "
               + $"elbow {elbow:0.0}° -> {arm.ElbowAngle:0.0}°)");
        Expect(!Bit("arm.inposition"),
               "and does not claim to have arrived, which is the only honest thing it can say");

        Tags.ClearForce("arm.fault");
        Run(400);
        Expect(Bit("arm.inposition"), "clearing the fault lets it finish the move it was on");
    }

    /// <summary>Perpendicular distance from <paramref name="point"/> to the
    /// segment a→b. The zero-length case is guarded rather than divided
    /// through: two identical poses are a real thing to be handed.</summary>
    private static float PerpendicularDistance(Vector3 a, Vector3 b, Vector3 point)
    {
        Vector3 ab = b - a;
        float length = ab.Length();
        if (length < 1e-6f) return point.DistanceTo(a);

        Vector3 dir = ab / length;
        float along = Mathf.Clamp((point - a).Dot(dir), 0.0f, length);
        return point.DistanceTo(a + dir * along);
    }

    // ---------- HA-02: the pattern

    private void PulseIndex(int times = 1)
    {
        for (int i = 0; i < times; i++)
        {
            Tags.Set("pallet.index", true);
            Run(2);
            Tags.Set("pallet.index", false);
            Run(2);
        }
    }

    /// <summary>The pattern walks a row and wraps to the next. Three columns at
    /// 0.24 and two rows at 0.28, so every coordinate below is arithmetic
    /// anybody can check by hand — which is the point of asserting positions
    /// rather than asserting that a counter moved.</summary>
    private void CheckPalletPatternWalksAndWraps()
    {
        var pallet = Part<PalletStation>("pallet");
        pallet.ChangePallet();
        Run(2);

        Expect(Whole("pallet.count") == 0, "a fresh pallet is empty");
        Expect(Whole("pallet.slot") == 0 && Whole("pallet.layer") == 0,
               "and the next carton goes in slot 0 of layer 0");
        Expect(Mathf.Abs((float)Num("pallet.nextx") + 0.24f) < 0.001f,
               $"at the near-left corner of the grid (x={Num("pallet.nextx"):0.000}, "
               + $"expected -0.240)");
        Expect(Mathf.Abs((float)Num("pallet.nextz") + 0.14f) < 0.001f,
               $"(z={Num("pallet.nextz"):0.000}, expected -0.140)");

        PulseIndex();
        Expect(Whole("pallet.count") == 1, "one index is one carton");
        Expect(Mathf.Abs((float)Num("pallet.nextx")) < 0.001f,
               $"and the next slot is one pitch along the row (x={Num("pallet.nextx"):0.000}, "
               + $"expected 0.000)");
        Expect(Mathf.Abs((float)Num("pallet.nextz") + 0.14f) < 0.001f,
               "on the same row");

        PulseIndex();
        Expect(Mathf.Abs((float)Num("pallet.nextx") - 0.24f) < 0.001f,
               $"and one more to the end of the row (x={Num("pallet.nextx"):0.000}, "
               + $"expected 0.240)");

        PulseIndex();
        Expect(Mathf.Abs((float)Num("pallet.nextx") + 0.24f) < 0.001f
               && Mathf.Abs((float)Num("pallet.nextz") - 0.14f) < 0.001f,
               $"and the fourth wraps back to the start of the *next* row "
               + $"({Num("pallet.nextx"):0.000}, {Num("pallet.nextz"):0.000}, expected "
               + $"-0.240, 0.140)");
        Expect(!Bit("pallet.layerdone"), "with the layer not finished yet");
    }

    /// <summary>A completed layer rises by exactly one layer height, and the
    /// odd layers are turned a quarter-turn — which is what stops a real stack
    /// shearing apart, and what stops "the pattern" being a list of six
    /// coordinates a program can hard-code.</summary>
    private void CheckPalletLayersRiseAndInterlock()
    {
        var pallet = Part<PalletStation>("pallet");

        float layerZeroY = (float)Num("pallet.nexty");
        PulseIndex(3);    // slots 3, 4 and 5 finish the six-slot layer

        Expect(Whole("pallet.count") == 6, $"six cartons fill a 3 × 2 layer "
                                           + $"({Whole("pallet.count")})");
        Expect(Bit("pallet.layerdone"),
               "and the station says the layer is complete — a level, so a slow scan cannot "
               + "miss it");
        Expect(Whole("pallet.layer") == 1 && Whole("pallet.slot") == 0,
               $"the next carton starts layer 1 at slot 0 "
               + $"(layer {Whole("pallet.layer")}, slot {Whole("pallet.slot")})");
        Expect(Mathf.Abs((float)Num("pallet.nexty") - (layerZeroY + 0.12f)) < 0.001f,
               $"exactly one layer height higher ({Num("pallet.nexty"):0.000} m against "
               + $"{layerZeroY + 0.12f:0.000})");

        // The interlock: layer 1 is the same six slots turned a quarter-turn,
        // so slot 0 of layer 1 is NOT above slot 0 of layer 0.
        Vector3 layerZeroSlotZero = pallet.SlotOffset(0, 0);
        Vector3 layerOneSlotZero = pallet.SlotOffset(0, 1);
        Expect(Mathf.Abs(layerOneSlotZero.X - layerZeroSlotZero.X) > 0.05f
               || Mathf.Abs(layerOneSlotZero.Z - layerZeroSlotZero.Z) > 0.05f,
               $"and slot 0 of layer 1 is not directly above slot 0 of layer 0 — the layers "
               + $"interlock ({layerZeroSlotZero} against {layerOneSlotZero})");
        Expect(Mathf.Abs((float)Num("pallet.nextx") + 0.14f) < 0.001f,
               $"the turned grid is 2 × 3 at the swapped pitches "
               + $"(x={Num("pallet.nextx"):0.000}, expected -0.140)");

        PulseIndex();
        Expect(!Bit("pallet.layerdone"),
               "and `layerdone` drops again as soon as the next layer is started");
    }

    /// <summary>A full pallet refuses. Not "reports full and carries on
    /// counting" — the position tags stop moving, so a program that ignores
    /// <c>full</c> places on the same slot rather than building into the air,
    /// and finds out.</summary>
    private void CheckPalletFillsAndRefuses()
    {
        var pallet = Part<PalletStation>("pallet");

        PulseIndex(pallet.Capacity);     // well past what is left
        Expect(Whole("pallet.count") == pallet.Capacity,
               $"the pallet takes exactly its capacity ({Whole("pallet.count")} of "
               + $"{pallet.Capacity})");
        Expect(Bit("pallet.full"), "and says it is full");

        float x = (float)Num("pallet.nextx");
        float y = (float)Num("pallet.nexty");
        int count = Whole("pallet.count");
        PulseIndex(3);
        Expect(Whole("pallet.count") == count,
               $"further index pulses are refused outright ({Whole("pallet.count")} against "
               + $"{count})");
        Expect(Mathf.IsEqualApprox((float)Num("pallet.nextx"), x)
               && Mathf.IsEqualApprox((float)Num("pallet.nexty"), y),
               "and the published position does not move on — a full pallet does not invite "
               + "a program to stack into the air");

        Tags.Set("pallet.change", true);
        Run(2);
        Tags.Set("pallet.change", false);
        Run(2);
        Expect(Whole("pallet.count") == 0 && !Bit("pallet.full"),
               "a pallet change starts an empty one");
        Expect(Whole("pallet.layer") == 0 && Whole("pallet.slot") == 0,
               "back at the first slot of the first layer");
    }

    /// <summary>`index` is an edge. Held high it advances once, because a
    /// controller that scans slowly should place one carton, not forty.</summary>
    private void CheckPalletIndexIsAnEdge()
    {
        Tags.Set("pallet.index", true);
        Run(200);
        Expect(Whole("pallet.count") == 1,
               $"an index held high for two hundred scans places exactly one carton "
               + $"({Whole("pallet.count")}) — it is an edge, not a level");
        Tags.Set("pallet.index", false);
        Run(2);
    }

    // ---------- HA-03: the rest of the lift, hand-turned

    private void CheckLiftClampsAnImpossibleCall()
    {
        var lift = Part<VerticalLift>("lift");

        Tags.Set("lift.target", 99);
        Run(600);
        Expect(lift.TargetLevel == lift.EffectiveLevels - 1,
               $"a call to a level the mast does not have goes to the top one "
               + $"({lift.TargetLevel} of 0..{lift.EffectiveLevels - 1})");
        Expect(Mathf.Abs((float)Num("lift.height")
                         - (lift.EffectiveLevels - 1) * LiftSpacing) < 0.05f,
               $"and the carriage stops there rather than climbing out of the mast "
               + $"({Num("lift.height"):0.000} m)");
    }

    private void CheckLiftSeizesBetweenFloors()
    {
        var lift = Part<VerticalLift>("lift");

        Tags.Set("lift.target", 0);
        Run(30);                                  // 0.5 s at 0.75 m/s: well short
        Tags.Force("lift.fault", true);
        float height = lift.Height;
        Run(600);

        Expect(Mathf.IsEqualApprox(lift.Height, height),
               $"a seized hoist stops between floors and stays there "
               + $"({height:0.000} m -> {lift.Height:0.000} m)");
        Expect(!Bit("lift.atlevel"),
               "and does not claim to be at a level, which is what blocks both of them");
        Expect(!Bit("lift.ready"), "nor ready to take anything");
        Expect(!lift.IsGateOpen,
               "with the gate shut — the one safe thing a lift stuck between floors can do");

        Tags.ClearForce("lift.fault");
        Run(600);
        Expect(Bit("lift.atlevel"), "clearing the fault lets it finish the move it was on");
    }
}
