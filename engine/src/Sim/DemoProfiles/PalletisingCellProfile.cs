using Godot;
using FactoryForge.TagBus;

namespace FactoryForge.Sim.DemoProfiles;

/// <summary>
/// The palletising cell (HA-04). Two things the other profiles never had to
/// do: solve for joint angles, and index through a pattern.
///
/// <b>The arm takes angles, the pallet publishes a place.</b> That gap is the
/// scene. <see cref="Parts.ArticulatedArm"/> deliberately does not accept a tip
/// target — an arm that did would hide everything that makes it an arm — so the
/// controller is the thing that has to turn "put it at x, y, z" into three axis
/// setpoints. <see cref="Solve"/> is that arithmetic, and it is about twenty
/// lines: a waist bearing, a law-of-cosines elbow, and a shoulder made of the
/// angle to the target plus the angle the upper arm stands off it. Every
/// student who writes this cell writes those twenty lines.
///
/// <b>It measures the arm before it uses it.</b> The link lengths are settings
/// on the part, so a profile that hardcoded them would silently place into thin
/// air the day somebody moved a slider. Instead the sequence starts by
/// commanding two known poses and reading the tool position back: straight out
/// gives the shoulder height and the sum of the links, folded to a right angle
/// gives the upper arm alone, and the forearm is the difference. That is a real
/// commissioning step and it costs two moves.
///
/// <b>The moves are staged, not point-to-point.</b> A joint-space move bows a
/// long way off the straight line between its endpoints — the arm's own
/// self-test measures 176 mm on a two-joint move — so a carried carton flown
/// directly from the pick to a slot would sweep through whatever is stacked in
/// between. So the sequence lifts, <em>swings on the waist alone</em> (which
/// moves the tool on an exact horizontal circle and cannot dip), aligns the
/// radius at transit height, and only then descends. That is the approach-point
/// discipline a real cell uses, and here it is not a convention — it is the fix
/// for a thing you can watch go wrong.
///
/// <b>It stops rather than guessing.</b> If <see cref="Solve"/> cannot reach a
/// slot, the line holds off and the tower goes yellow. A controller that
/// quietly clamped to its nearest reachable pose would place a carton beside
/// the pallet and carry on.
/// </summary>
public sealed class PalletisingCellProfile : IDemoProfile
{
    private enum Step
    {
        MeasureStretch,   // straight out: shoulder height, and L1 + L2
        MeasureFold,      // folded to a right angle: L1 on its own
        ToPick,           // waist to the infeed, tool above the indexed carton
        Descend,
        Grip,
        Lift,
        Swing,            // waist alone, so the tool travels a level circle
        Align,            // radius at transit height, over the slot
        Place,
        Release,
        Retreat,
        ChangePallet,
    }

    // --- where the cell's furniture is, in the arm's own frame. Template
    // knowledge, the same way the gantry profile's PickAt/PlaceAt percentages
    // are: arm at (-0.57, -0.85), pick stop at (-0.57, 0.0), pallet centre at
    // (0.10, -0.85), all on the work plane.

    /// <summary>Pick stop, arm-local. Straight across the +Z side.</summary>
    private const float PickLocalZ = 0.85f;

    /// <summary>Pallet centre, arm-local. Straight out the +X side.</summary>
    private const float PalletLocalX = 0.67f;

    /// <summary>Tool height for a pick off the belt: a short carton rests with
    /// its centre 0.11 m above the work plane and the jaws reach around
    /// it.</summary>
    private const float PickY = 0.16f;

    /// <summary>Transit height. Above a full three-layer stack by more than the
    /// 0.12 m a carried carton hangs below the tool — that clearance is the
    /// whole reason this height is a constant and not "a bit above the
    /// slot".</summary>
    private const float TransitY = 0.72f;

    /// <summary>How far above the drop surface the tool sits so the carton's
    /// underside is level with it. <c>CarryHeldItem</c> holds a carton half its
    /// height plus 20 mm below the tool, so this is that plus the other half,
    /// plus a millimetre of daylight.</summary>
    private const float CartonDrop = 0.13f;

    /// <summary>Degrees of axis error counted as arrived. Wider than the
    /// machine's own in-position window so the two never disagree in a way that
    /// stalls the sequence.</summary>
    private const float ArrivalWindow = 1.8f;

    /// <summary>Shortest link the solver will divide by.</summary>
    private const float MinLink = 0.05f;

    private const double FeedInterval = 1.6;

    private readonly OperatorStation _station =
        new OperatorStation("panel", "arm.fault", "infeed.fault", "stop.fault");

    private Step _step;
    private double _feedTimer;
    private double _settle;
    private bool _prevIndex;

    // Measured from the machine rather than copied from the template.
    private float _shoulderHeight = 0.78f;
    private float _upperArm = 0.72f;
    private float _forearm = 0.58f;
    private float _totalReach = 1.30f;

    // The pose currently commanded, so a waist-only swing can hold the other
    // two exactly where they are rather than re-solving and drifting.
    private float _waistCmd;
    private float _shoulderCmd;
    private float _elbowCmd = 90.0f;

    /// <summary>The slot this cycle is placing into, captured when the carton
    /// is picked. Read once rather than every scan: the pallet's published
    /// position moves the instant <c>index</c> is pulsed, and a sequence still
    /// reading it would chase the next slot half-way through releasing into
    /// this one.</summary>
    private Vector3 _slot;

    private bool _unreachable;

    public void Start(TagTable tags)
    {
        _station.Begin();
        _step = Step.MeasureStretch;
        _feedTimer = 1.0;
        _settle = 0.0;
        _prevIndex = false;
        _unreachable = false;

        Command(tags, 0.0f, 0.0f, 0.0f);
        OperatorStation.Set(tags, "arm.grip", false);
        OperatorStation.Set(tags, "pallet.index", false);
        OperatorStation.Set(tags, "pallet.change", false);
        OperatorStation.Set(tags, "emitter.emit", false);
        _station.Lamps(tags);
    }

    public void Tick(double delta, TagTable tags)
    {
        _station.Scan(tags);

        // A pose the arm cannot reach stops the line. Not a clamp: clamping
        // would place a carton beside the pallet and keep going, which is the
        // failure a reach check exists to prevent.
        if (_unreachable) _station.HoldOff();

        _station.Lamps(tags);
        OperatorStation.Set(tags, "onpallet.value", (int)OperatorStation.Num(tags, "pallet.count"));

        bool running = _station.Running;
        DriveInfeed(delta, tags, running);

        // An index pulse is exactly one scan wide. Cleared here, at the top,
        // so every `Set(..., true)` below is a genuine rising edge whatever
        // order the steps run in.
        if (_prevIndex) OperatorStation.Set(tags, "pallet.index", false);
        _prevIndex = false;

        if (!running)
        {
            // Stopped means stopped. The jaws stay as they are, so an E-stop
            // does not drop a carried carton on the floor, but nothing new
            // starts moving.
            return;
        }

        RunSequence(delta, tags);
    }

    private void DriveInfeed(double delta, TagTable tags, bool running)
    {
        // The blade stays up the whole time. It is not a gate the sequence
        // opens and closes -- it is what makes "the carton is at the pick
        // point" true, so the arm reaches for a place rather than for a guess.
        OperatorStation.Set(tags, "stop.raise", running);

        bool atPick = OperatorStation.Bit(tags, "atpick.detect");

        OperatorStation.Set(tags, "infeed.run", running);
        OperatorStation.Set(tags, "infeed.speed", running ? _station.Setpoint(tags, 70.0) : 0.0);
        // The pick belt runs even with a carton indexed: it is what holds the
        // queue against the blade, and stopping it is the accumulation
        // interlock that wedges a carton on the joint between two decks
        // (gotcha 23).
        OperatorStation.Set(tags, "pickstation.rotate", running);

        OperatorStation.Set(tags, "emitter.emit", false);
        if (!running) return;

        // Feed into space rather than on a clock alone: nothing new while one
        // is already indexed at the stop.
        if (atPick) return;

        _feedTimer -= delta;
        if (_feedTimer > 0.0) return;
        _feedTimer = FeedInterval;
        OperatorStation.Set(tags, "emitter.emit", true);
    }

    // ---------- the sequence

    private void RunSequence(double delta, TagTable tags)
    {
        bool holding = OperatorStation.Bit(tags, "arm.holding");

        switch (_step)
        {
            case Step.MeasureStretch:
                Command(tags, 0.0f, 0.0f, 0.0f);
                if (!Arrived(tags)) break;
                // Straight out and level: the tool is at shoulder height, at a
                // radius of both links end to end.
                _shoulderHeight = (float)OperatorStation.Num(tags, "arm.height");
                _totalReach = (float)OperatorStation.Num(tags, "arm.reach");
                Command(tags, 0.0f, 0.0f, 90.0f);
                _step = Step.MeasureFold;
                break;

            case Step.MeasureFold:
                if (!Arrived(tags)) break;
                // Folded to a right angle the forearm contributes no radius at
                // all, so what is left is the upper arm.
                _upperArm = (float)OperatorStation.Num(tags, "arm.reach");
                _forearm = _totalReach - _upperArm;
                _step = Step.ToPick;
                break;

            case Step.ToPick:
                OperatorStation.Set(tags, "arm.grip", false);
                if (!MoveTo(tags, new Vector3(0.0f, TransitY, PickLocalZ))) break;
                if (!Arrived(tags)) break;
                if (!OperatorStation.Bit(tags, "atpick.detect")) break;
                // A short dwell so a carton still sliding into the blade has
                // settled before the jaws come down around it.
                _settle = 0.35;
                _step = Step.Descend;
                break;

            case Step.Descend:
                _settle -= delta;
                if (_settle > 0.0) break;
                if (!MoveTo(tags, new Vector3(0.0f, PickY, PickLocalZ))) break;
                if (!Arrived(tags)) break;
                _settle = 0.2;
                _step = Step.Grip;
                break;

            case Step.Grip:
                OperatorStation.Set(tags, "arm.grip", true);
                _settle -= delta;
                if (_settle > 0.0) break;
                // The check that matters: jaws that closed on nothing go back
                // to waiting instead of flying an empty cycle and indexing the
                // pattern past a slot that never got a carton.
                if (holding)
                {
                    _slot = SlotTarget(tags);
                    _step = Step.Lift;
                }
                else
                {
                    OperatorStation.Set(tags, "arm.grip", false);
                    _step = Step.ToPick;
                }
                break;

            case Step.Lift:
                if (!MoveTo(tags, new Vector3(0.0f, TransitY, PickLocalZ))) break;
                if (!Arrived(tags)) break;
                _step = Step.Swing;
                break;

            case Step.Swing:
                // Waist only. Rotating one joint moves the tool on an exact
                // horizontal circle, so a carried carton cannot dip into the
                // stack on the way across -- which a two-joint move to the same
                // endpoint genuinely can.
                Command(tags, WaistFor(_slot), _shoulderCmd, _elbowCmd);
                if (!Arrived(tags)) break;
                _step = Step.Align;
                break;

            case Step.Align:
                if (!MoveTo(tags, WithHeight(_slot, TransitY))) break;
                if (!Arrived(tags)) break;
                _step = Step.Place;
                break;

            case Step.Place:
                if (!MoveTo(tags, _slot)) break;
                if (!Arrived(tags)) break;
                _step = Step.Release;
                break;

            case Step.Release:
                OperatorStation.Set(tags, "arm.grip", false);
                if (holding) break;
                // The index is the handshake: one carton placed, advance the
                // pattern. Raised here and cleared at the top of the next scan,
                // because the station takes a rising edge and a level would
                // index the whole pallet in a second.
                OperatorStation.Set(tags, "pallet.index", true);
                _prevIndex = true;
                _step = Step.Retreat;
                break;

            case Step.Retreat:
                if (!MoveTo(tags, WithHeight(_slot, TransitY))) break;
                if (!Arrived(tags)) break;
                _step = OperatorStation.Bit(tags, "pallet.full") ? Step.ChangePallet : Step.ToPick;
                break;

            case Step.ChangePallet:
                // The full pallet drives away and an empty one arrives. A pulse,
                // not a level: held high it would clear the pallet every scan
                // and nothing would ever be stacked.
                if (!OperatorStation.Bit(tags, "pallet.change"))
                {
                    OperatorStation.Set(tags, "pallet.change", true);
                    break;
                }
                OperatorStation.Set(tags, "pallet.change", false);
                if (!OperatorStation.Bit(tags, "pallet.full")) _step = Step.ToPick;
                break;
        }
    }

    /// <summary>Where the next carton goes, in the arm's frame. The station
    /// publishes it in its own, and the offset between the two parts is the one
    /// piece of template knowledge this needs — both origins sit on the work
    /// plane, which is what makes the Y numbers directly comparable.</summary>
    private static Vector3 SlotTarget(TagTable tags) => new(
        PalletLocalX + (float)OperatorStation.Num(tags, "pallet.nextx"),
        (float)OperatorStation.Num(tags, "pallet.nexty") + CartonDrop,
        (float)OperatorStation.Num(tags, "pallet.nextz"));

    private static Vector3 WithHeight(Vector3 target, float y) => new(target.X, y, target.Z);

    private static float WaistFor(Vector3 target) =>
        Mathf.RadToDeg(Mathf.Atan2(-target.Z, target.X));

    /// <summary>Solve for the pose and command it. False when the point cannot
    /// be reached, which latches the line off rather than moving anywhere.</summary>
    private bool MoveTo(TagTable tags, Vector3 target)
    {
        if (!Solve(target, out float waist, out float shoulder, out float elbow))
        {
            _unreachable = true;
            return false;
        }
        Command(tags, waist, shoulder, elbow);
        return true;
    }

    private void Command(TagTable tags, float waist, float shoulder, float elbow)
    {
        _waistCmd = waist;
        _shoulderCmd = shoulder;
        _elbowCmd = elbow;
        OperatorStation.Set(tags, "arm.waist", (double)waist);
        OperatorStation.Set(tags, "arm.shoulder", (double)shoulder);
        OperatorStation.Set(tags, "arm.elbow", (double)elbow);
    }

    /// <summary>
    /// Has every axis reached the pose this step commanded?
    ///
    /// Deliberately <em>not</em> <c>arm.inposition</c>. That bit compares the
    /// axes to the pose the machine currently holds, and a pose written this
    /// scan does not reach the machine until the next tick — so on the scan
    /// that issues a move it still reads "arrived", at the pose we are trying
    /// to leave. The gantry cell's sequencer trusted the equivalent bit and
    /// released every carton straight back onto the pick station, having never
    /// travelled at all. Checking the angle feedback against the pose this step
    /// wants has no such window.
    /// </summary>
    private bool Arrived(TagTable tags) =>
        Mathf.Abs((float)OperatorStation.Num(tags, "arm.atwaist") - _waistCmd) <= ArrivalWindow
        && Mathf.Abs((float)OperatorStation.Num(tags, "arm.atshoulder") - _shoulderCmd) <= ArrivalWindow
        && Mathf.Abs((float)OperatorStation.Num(tags, "arm.atelbow") - _elbowCmd) <= ArrivalWindow;

    /// <summary>
    /// Inverse kinematics for a waist and two links: turn a point in the arm's
    /// own frame into the three angles that put the tool there.
    ///
    /// The elbow comes from the law of cosines on the triangle
    /// shoulder–elbow–tool, and the shoulder is the angle up to the target plus
    /// the angle the upper arm stands off that line. There are always two
    /// solutions — elbow up and elbow down — and this takes elbow-up, which is
    /// the one that keeps the forearm out of whatever the arm is reaching over.
    ///
    /// Three guards, in the order they bite, because a non-finite float now
    /// throws at <c>TagTable.Set</c> and every one of these is reachable from a
    /// scene somebody edited:
    /// <list type="bullet">
    /// <item>a zero-length link would divide the elbow term by zero;</item>
    /// <item>a target on the shoulder axis itself makes <c>d</c> zero and
    /// divides the shoulder term by it;</item>
    /// <item>a target further away than the links reach has no solution at all,
    /// and <c>acos</c> of the number that produces is NaN. The clamp on the
    /// cosines would hide that as a silently wrong pose, so the reach is
    /// checked first and refused — this is the case that actually happens.</item>
    /// </list>
    /// </summary>
    private bool Solve(Vector3 target, out float waist, out float shoulder, out float elbow)
    {
        waist = WaistFor(target);
        shoulder = 0.0f;
        elbow = 90.0f;

        if (_upperArm < MinLink || _forearm < MinLink) return false;

        float radius = Mathf.Sqrt(target.X * target.X + target.Z * target.Z);
        float rise = target.Y - _shoulderHeight;
        float distance = Mathf.Sqrt(radius * radius + rise * rise);

        if (distance < MinLink) return false;
        if (distance > _upperArm + _forearm) return false;

        float cosElbow = (distance * distance - _upperArm * _upperArm - _forearm * _forearm)
                         / (2.0f * _upperArm * _forearm);
        float cosOffset = (distance * distance + _upperArm * _upperArm - _forearm * _forearm)
                          / (2.0f * distance * _upperArm);

        elbow = Mathf.RadToDeg(Mathf.Acos(Mathf.Clamp(cosElbow, -1.0f, 1.0f)));
        float offset = Mathf.Acos(Mathf.Clamp(cosOffset, -1.0f, 1.0f));
        shoulder = Mathf.RadToDeg(Mathf.Atan2(rise, radius) + offset);
        return true;
    }

    /// <summary>Which step the sequence is on, as a number a test can ask for.
    /// Not a tag — the cell has no display for it — but "the sequence got past
    /// measuring the arm" is the difference between a cell that works and one
    /// that is merely not throwing.</summary>
    public int Stage => (int)_step;
}
