using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// A three-axis jointed arm — waist, shoulder, elbow — with a gripper (HA-01).
///
/// <b>Why this is not the gantry again.</b> <see cref="PickPlaceArm"/> is three
/// orthogonal motions: travel is X, stroke is Y, and each one moves the tool in
/// its own direction and nobody else's. Every position it can hold is a
/// position it can be commanded to directly, so the only thing to learn is the
/// order the three are sequenced in. An arm is the opposite. The tool's
/// position is a function of <em>all three</em> joints at once, no joint moves
/// the tool in a straight line, and large parts of the space around it cannot
/// be reached at all. Those are the two lessons the gantry structurally cannot
/// teach, and they are the two most expensive things to discover on a real
/// machine.
///
/// <b>Joint angles, not a tip target.</b> This was the design decision worth
/// arguing about, and it went to joint angles for three reasons. First, a tip
/// target is what the gantry already offers — <c>target</c> percent plus lower
/// plus grip — so an arm that took one would teach pick-and-place sequencing a
/// second time and nothing else. Second, an arm driven by a tip target hides
/// exactly what makes it an arm: the controller would command a point, the part
/// would solve for the joints, and the student would never see that a straight
/// command produces a curved path, nor that the same point can be reached in
/// two poses, nor that some points cannot be reached at all. Third, joint
/// setpoints are what a controller genuinely sends: a PLC talking to a robot
/// over a fieldbus moves axes, and the interpolation and the reach checking are
/// the program's problem. So the tags are three angles in, three angles back,
/// and the forward kinematics — where the tool actually ended up — published as
/// a measurement the program can check its own arithmetic against.
///
/// <b>Reach limits and the wrist singularity, made observable.</b> Each joint
/// has a mechanical stop. A command past one is clamped and <c>limit</c> says
/// so, rather than the axis quietly going where it was told; that is the
/// "refuses" a student has to write an interlock around. And <c>stretch</c> —
/// the tool's horizontal reach as a percentage of the arm's maximum — is the
/// singularity indicator: near 100 % the elbow is straight, and a whole degree
/// of elbow then moves the tool by a fraction of a millimetre, so an arm asked
/// to work out there has lost the authority to position itself radially at all.
/// It is a real effect of the geometry here, not a lamp that lights on a number.
///
/// Local space: the origin is the centre of the base on the work plane, so the
/// arm drops onto the grid at the same height as everything else and its own
/// pedestal reaches down to the floor. Waist 0° faces +X.
/// </summary>
public partial class ArticulatedArm : Node3D, IPart
{
    /// <summary>Shoulder-to-elbow link, metres.</summary>
    [Export] public float UpperArm { get; set; } = 0.62f;

    /// <summary>Elbow-to-tool link, metres.</summary>
    [Export] public float Forearm { get; set; } = 0.48f;

    /// <summary>
    /// Height of the shoulder pivot above the part origin.
    ///
    /// Tall enough that the parked pose — upper arm horizontal, elbow bent a
    /// right angle — puts the tool <em>above</em> the work plane rather than
    /// below it. The first version was 0.46 against a 0.48 forearm, which drew
    /// a perfectly correct arm with its gripper 20 mm inside the conveyor it
    /// was meant to be picking from. A headless test cannot see that; a
    /// rendered frame can.
    /// </summary>
    [Export] public float ShoulderHeight { get; set; } = 0.78f;

    /// <summary>Axis rate at full command, degrees per second. One rate for all
    /// three on purpose: a real arm's axes differ, and every one of those
    /// differences is a reason the tool path bends. One rate makes the bend a
    /// property of the <em>geometry</em>, which is the thing being taught.</summary>
    [Export] public float JointSpeed { get; set; } = 55.0f;

    /// <summary>How close to a commanded angle counts as in position, degrees.
    /// A real axis has a window; without one <c>inposition</c> would chatter and
    /// a sequence written against it would never advance.</summary>
    [Export] public float PositionTolerance { get; set; } = 1.0f;

    /// <summary>Waist travel either side of centre, degrees. Not 180: an arm
    /// with cables through it cannot spin, and the dead sector behind it is
    /// part of what makes reach a real constraint.</summary>
    [Export] public float WaistLimit { get; set; } = 165.0f;

    /// <summary>Shoulder stops, degrees from horizontal, positive up.</summary>
    [Export] public float ShoulderMin { get; set; } = -35.0f;
    [Export] public float ShoulderMax { get; set; } = 105.0f;

    /// <summary>Elbow bend from straight, degrees. Zero is a straight arm —
    /// full stretch — and it cannot go past it, which is why asking for more
    /// reach than the links have is refused rather than obeyed.</summary>
    [Export] public float ElbowMax { get; set; } = 150.0f;

    /// <summary>The smallest link this part will build. A zero-length link is
    /// not a smaller arm, it is a divide by zero in <see cref="Stretch"/> and a
    /// NaN on the way to <c>TagTable.Set</c>, which now throws (HP-23). Clamped
    /// here, at the source, rather than guarded at each use.</summary>
    private const float MinLink = 0.05f;

    private const float GripHalfOpen = 0.075f;

    /// <summary>Half a standard carton's width, for how far the jaws close on
    /// one. A held carton is carried by <see cref="CarryHeldItem"/> rather than
    /// by the jaws, so this is what the gripper <em>looks</em> like — but it
    /// has to look like it is gripping the thing it is carrying.</summary>
    private const float GripHalfClosedOnCarton = 0.24f / 2.0f + 0.011f;

    private Node3D _waist = null!;
    private Node3D _shoulder = null!;
    private Node3D _elbow = null!;
    private Node3D _tool = null!;
    private Node3D _fingerNear = null!;
    private Node3D _fingerFar = null!;
    private Area3D _pickZone = null!;
    private StandardMaterial3D _toolMat = null!;
    private StandardMaterial3D? _faultLampMat;

    private float _waistAngle;
    private float _shoulderAngle;
    private float _elbowAngle = 90.0f;

    private float _waistTarget;
    private float _shoulderTarget;
    private float _elbowTarget = 90.0f;

    private bool _clamped;

    private BoxPhysics? _held;
    private Vector3 _lastToolWorld;
    private bool _hasLastToolWorld;
    private Vector3 _toolVelocity;

    public float WaistAngle => _waistAngle;
    public float ShoulderAngle => _shoulderAngle;
    public float ElbowAngle => _elbowAngle;

    /// <summary>The arm's maximum horizontal reach, straight out. Never zero:
    /// the links are clamped to <see cref="MinLink"/>.</summary>
    public float MaxReach => Mathf.Max(UpperArm + Forearm, 2.0f * MinLink);

    /// <summary>
    /// Where the tool actually is, in the part's own frame, read off the scene
    /// graph rather than recomputed from a formula.
    ///
    /// That is deliberate. A second copy of the kinematics would be a second
    /// thing to keep in step with the geometry, and the two disagreeing is the
    /// bug nobody would find — the arm would draw itself in one place and tell
    /// the PLC it was in another.
    /// </summary>
    public Vector3 ToolLocal =>
        _tool is null ? Vector3.Zero : GlobalTransform.AffineInverse() * _tool.GlobalPosition;

    /// <summary>Horizontal distance from the waist axis to the tool, metres.</summary>
    public float Reach
    {
        get
        {
            Vector3 tool = ToolLocal;
            return Mathf.Sqrt(tool.X * tool.X + tool.Z * tool.Z);
        }
    }

    /// <summary>Tool height above the work plane, metres. Negative below it —
    /// an arm reaching into a chute is a normal thing to ask for.</summary>
    public float ToolHeight => ToolLocal.Y;

    /// <summary>Reach as a percentage of the maximum. The singularity
    /// indicator: at 100 % the elbow is straight and a degree of it is worth
    /// almost no radial motion at all.</summary>
    public float Stretch => Reach / MaxReach * 100.0f;

    /// <summary>
    /// True while a commanded angle is outside a joint's mechanical stop.
    ///
    /// Note this is about the <em>command</em>, not the position: it is high
    /// from the scan the impossible command is written, before the axis has
    /// moved anywhere, which is what makes it usable as an interlock rather
    /// than as a post-mortem.
    /// </summary>
    public bool IsAtLimit => _clamped;

    /// <summary>
    /// Every axis is inside its window of the angle the machine currently
    /// holds.
    ///
    /// Same trap as <see cref="PickPlaceArm.InPosition"/>, and it is worth
    /// repeating because it costs an hour every time: a target written this
    /// scan does not reach the machine until the next physics tick, so on the
    /// scan that commands a move this bit still reads "arrived" — at the pose
    /// you are trying to leave. Check the angle feedback against the pose the
    /// step wants, not this bit alone.
    /// </summary>
    public bool InPosition =>
        Mathf.Abs(_waistAngle - _waistTarget) <= PositionTolerance
        && Mathf.Abs(_shoulderAngle - _shoulderTarget) <= PositionTolerance
        && Mathf.Abs(_elbowAngle - _elbowTarget) <= PositionTolerance;

    public bool IsHolding => _held is not null;

    /// <summary>Seized: every axis stops where it stands. An arm frozen
    /// half-way through a move, with its tool somewhere no limit switch
    /// describes, is exactly the failure worth being able to cause on
    /// purpose.</summary>
    public bool IsFaulted { get; private set; }

    public override void _Ready()
    {
        UpperArm = Mathf.Max(MinLink, UpperArm);
        Forearm = Mathf.Max(MinLink, Forearm);

        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.24f, 0.26f, 0.29f),
            Metallic = 0.45f,
            Roughness = 0.48f,
        };
        // Robot yellow, and brushed rather than glossy for the same reason the
        // gantry's rail is: at high metallic and low roughness the sun blows a
        // large flat link out to a solid white slab.
        var linkMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.90f, 0.68f, 0.09f),
            Metallic = 0.25f,
            Roughness = 0.50f,
        };
        var jointMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.16f, 0.17f, 0.19f),
            Metallic = 0.40f,
            Roughness = 0.42f,
        };

        BuildPedestal(frameMat);

        const float turretY = 0.10f;

        _waist = new Node3D { Name = "Waist", Position = new Vector3(0, turretY, 0) };
        AddChild(_waist);

        _waist.AddChild(new MeshInstance3D
        {
            Name = "Turret",
            Mesh = new BoxMesh { Size = new Vector3(0.27f, 0.22f, 0.28f) },
            MaterialOverride = frameMat,
            Position = new Vector3(0, 0.12f, 0),
        });
        // A flash of colour on the front of the turret, so which way the waist
        // is pointing is legible at a glance. A symmetrical box is not.
        _waist.AddChild(new MeshInstance3D
        {
            Name = "TurretFace",
            Mesh = new BoxMesh { Size = new Vector3(0.02f, 0.14f, 0.20f) },
            MaterialOverride = linkMat,
            Position = new Vector3(0.16f, 0.12f, 0),
        });

        float shoulderLocalY = Mathf.Max(0.16f, ShoulderHeight - turretY);
        _shoulder = new Node3D { Name = "Shoulder", Position = new Vector3(0, shoulderLocalY, 0) };
        _waist.AddChild(_shoulder);

        _shoulder.AddChild(new MeshInstance3D
        {
            Name = "ShoulderHub",
            Mesh = new CylinderMesh { TopRadius = 0.072f, BottomRadius = 0.072f, Height = 0.17f },
            MaterialOverride = jointMat,
            RotationDegrees = new Vector3(90, 0, 0),
        });
        _shoulder.AddChild(new MeshInstance3D
        {
            Name = "UpperArmLink",
            Mesh = new BoxMesh { Size = new Vector3(UpperArm, 0.115f, 0.125f) },
            MaterialOverride = linkMat,
            Position = new Vector3(UpperArm / 2.0f, 0, 0),
        });

        _elbow = new Node3D { Name = "Elbow", Position = new Vector3(UpperArm, 0, 0) };
        _shoulder.AddChild(_elbow);

        _elbow.AddChild(new MeshInstance3D
        {
            Name = "ElbowHub",
            Mesh = new CylinderMesh { TopRadius = 0.056f, BottomRadius = 0.056f, Height = 0.14f },
            MaterialOverride = jointMat,
            RotationDegrees = new Vector3(90, 0, 0),
        });
        _elbow.AddChild(new MeshInstance3D
        {
            Name = "ForearmLink",
            Mesh = new BoxMesh { Size = new Vector3(Forearm, 0.088f, 0.10f) },
            MaterialOverride = linkMat,
            Position = new Vector3(Forearm / 2.0f, 0, 0),
        });

        BuildTool(jointMat);
        BuildFaultLamp();
        ApplyPose();
    }

    private void BuildPedestal(StandardMaterial3D frameMat)
    {
        float pedestalHeight = PartLayout.FloorDrop + 0.10f;
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new CylinderMesh { TopRadius = 0.235f, BottomRadius = 0.235f, Height = 0.035f },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "Pedestal",
            Mesh = new CylinderMesh
            {
                TopRadius = 0.145f, BottomRadius = 0.195f, Height = pedestalHeight,
            },
            MaterialOverride = frameMat,
            Position = new Vector3(0, -PartLayout.FloorDrop + pedestalHeight / 2.0f, 0),
        });
    }

    /// <summary>
    /// The tool: a two-finger gripper that picks a real
    /// <see cref="BoxPhysics"/>, exactly as the gantry's cup does — frozen
    /// kinematic and carried, then handed back to free physics with the tool's
    /// own measured velocity, so a carton let go mid-swing flies off on the
    /// tangent instead of dropping straight down.
    ///
    /// Fingers that visibly close are not decoration. A gripper drawn open
    /// while it is carrying something is a machine whose state you can only
    /// read in the tag list, and the whole argument for a 3D simulator is that
    /// you should not have to.
    /// </summary>
    private void BuildTool(StandardMaterial3D jointMat)
    {
        _tool = new Node3D { Name = "Tool", Position = new Vector3(Forearm, 0, 0) };
        _elbow.AddChild(_tool);

        _toolMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.18f, 0.19f, 0.21f),
            Metallic = 0.35f,
            Roughness = 0.45f,
        };

        _tool.AddChild(new MeshInstance3D
        {
            Name = "Wrist",
            Mesh = new BoxMesh { Size = new Vector3(0.10f, 0.11f, 0.13f) },
            MaterialOverride = jointMat,
        });

        _fingerNear = new Node3D { Name = "FingerNear" };
        _fingerFar = new Node3D { Name = "FingerFar" };
        _tool.AddChild(_fingerNear);
        _tool.AddChild(_fingerFar);

        foreach (var finger in new[] { _fingerNear, _fingerFar })
        {
            finger.AddChild(new MeshInstance3D
            {
                Name = "Jaw",
                Mesh = new BoxMesh { Size = new Vector3(0.07f, 0.16f, 0.032f) },
                MaterialOverride = _toolMat,
                Position = new Vector3(0.055f, -0.075f, 0),
            });
        }

        // What the jaws can reach. An Area3D rather than a distance search, so
        // a pick obeys the same collision world everything else does — and
        // sized to a carton rather than to a point, because a gripper that
        // demands the tool be within 50 mm of the carton's centre turns every
        // small pose error into a cycle carrying air.
        _pickZone = new Area3D { Name = "PickZone", Monitoring = true };
        _pickZone.AddChild(new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = new Vector3(0.26f, 0.16f, 0.26f) },
            Position = new Vector3(0.06f, -0.07f, 0),
        });
        _tool.AddChild(_pickZone);

        ApplyGripVisual(false);
    }

    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        // On the fixed pedestal, not on the waist: a beacon that swings out of
        // sight when the arm turns away is a beacon nobody sees.
        var mount = new Vector3(0, 0.02f, 0.24f);
        const float stalk = 0.22f;

        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultStalk",
            Mesh = new CylinderMesh { TopRadius = 0.008f, BottomRadius = 0.010f, Height = stalk },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.22f, 0.23f, 0.25f),
                Metallic = 0.40f,
                Roughness = 0.50f,
            },
            Position = mount + new Vector3(0, stalk / 2, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.038f, Height = 0.076f },
            MaterialOverride = _faultLampMat,
            Position = mount + new Vector3(0, stalk + 0.026f, 0),
        });
    }

    public void SetFaulted(bool faulted)
    {
        if (faulted == IsFaulted && _faultLampMat is not null) return;
        IsFaulted = faulted;

        if (_faultLampMat is null) return;
        _faultLampMat.AlbedoColor = faulted ? new Color(1.0f, 0.15f, 0.12f) : new Color(0.30f, 0.06f, 0.06f);
        _faultLampMat.EmissionEnabled = faulted;
        _faultLampMat.Emission = faulted ? new Color(1.0f, 0.15f, 0.12f) : Colors.Black;
        _faultLampMat.EmissionEnergyMultiplier = faulted ? 2.4f : 0.0f;
    }

    /// <summary>
    /// One machine tick: clamp the commands to the mechanical stops, drive each
    /// axis toward its own, then the gripper — in that order, because the
    /// gripper's decision depends on where the tool ended up this tick and not
    /// where it was last one.
    /// </summary>
    public void Step(float waist, float shoulder, float elbow, bool grip, float delta)
    {
        float waistWanted = Mathf.Clamp(waist, -WaistLimit, WaistLimit);
        float shoulderWanted = Mathf.Clamp(shoulder, ShoulderMin, ShoulderMax);
        float elbowWanted = Mathf.Clamp(elbow, 0.0f, ElbowMax);

        const float Slack = 0.5f;
        _clamped = Mathf.Abs(waistWanted - waist) > Slack
                   || Mathf.Abs(shoulderWanted - shoulder) > Slack
                   || Mathf.Abs(elbowWanted - elbow) > Slack;

        _waistTarget = waistWanted;
        _shoulderTarget = shoulderWanted;
        _elbowTarget = elbowWanted;

        if (!IsFaulted)
        {
            float step = JointSpeed * delta;
            _waistAngle = Mathf.MoveToward(_waistAngle, _waistTarget, step);
            _shoulderAngle = Mathf.MoveToward(_shoulderAngle, _shoulderTarget, step);
            _elbowAngle = Mathf.MoveToward(_elbowAngle, _elbowTarget, step);
        }

        ApplyPose();
        TrackToolVelocity(delta);
        UpdateGrip(grip);
        CarryHeldItem();
    }

    private void ApplyPose()
    {
        if (_waist is null) return;

        _waist.Rotation = new Vector3(0, Mathf.DegToRad(_waistAngle), 0);
        // +Z rotation swings +X toward +Y, so a positive shoulder angle lifts
        // the arm. The elbow bends the other way, which is what "bend from
        // straight" means and why its own limit is one-sided.
        _shoulder.Rotation = new Vector3(0, 0, Mathf.DegToRad(_shoulderAngle));
        _elbow.Rotation = new Vector3(0, 0, -Mathf.DegToRad(_elbowAngle));
    }

    /// <summary>The tool's own world velocity, measured rather than derived, so
    /// a carton released mid-swing is thrown with the speed the machine
    /// actually had. A released box that simply drops is the tell that a
    /// simulator is faking the handoff.</summary>
    private void TrackToolVelocity(float delta)
    {
        Vector3 now = _tool.GlobalPosition;
        // A flag rather than "is the last sample still zero": the origin is a
        // place the tool can legitimately be.
        if (delta > 0.0f && _hasLastToolWorld) _toolVelocity = (now - _lastToolWorld) / delta;
        _lastToolWorld = now;
        _hasLastToolWorld = true;
    }

    private void UpdateGrip(bool grip)
    {
        if (grip && _held is null) TryPick();
        else if (!grip && _held is not null) Release();

        ApplyGripVisual(grip);
    }

    private void ApplyGripVisual(bool closed)
    {
        if (_fingerNear is null) return;

        // Closed on a held carton, closed on nothing if the jaws were shut with
        // nothing between them — which is exactly what a gripper does, and
        // exactly why `holding` is a separate bit from the command.
        float half = closed ? (IsHolding ? GripHalfClosedOnCarton : 0.018f) : GripHalfOpen;
        _fingerNear.Position = new Vector3(0, 0, -half);
        _fingerFar.Position = new Vector3(0, 0, half);

        if (_toolMat is null) return;
        _toolMat.AlbedoColor = IsHolding ? new Color(0.35f, 0.12f, 0.10f) : new Color(0.18f, 0.19f, 0.21f);
        _toolMat.EmissionEnabled = IsHolding;
        _toolMat.Emission = new Color(1.0f, 0.35f, 0.15f);
        _toolMat.EmissionEnergyMultiplier = IsHolding ? 0.7f : 0.0f;
    }

    private void TryPick()
    {
        if (_pickZone is null) return;

        BoxPhysics? best = null;
        float nearest = float.MaxValue;
        foreach (var body in _pickZone.GetOverlappingBodies())
        {
            if (body is not BoxPhysics box || box.Freeze) continue;
            float d = box.GlobalPosition.DistanceSquaredTo(_tool.GlobalPosition);
            if (d >= nearest) continue;
            nearest = d;
            best = box;
        }

        if (best is null) return;

        // Kinematic rather than Static: a carried carton still shoves whatever
        // it is dragged into, which is how a real arm knocks a stack over.
        best.FreezeMode = RigidBody3D.FreezeModeEnum.Kinematic;
        best.Freeze = true;
        _held = best;
    }

    private void Release()
    {
        if (_held is null) return;
        var box = _held;
        _held = null;

        if (!IsInstanceValid(box)) return;
        box.Freeze = false;
        box.LinearVelocity = _toolVelocity;
        box.AngularVelocity = Vector3.Zero;
    }

    private void CarryHeldItem()
    {
        if (_held is null) return;
        // The carton may have been despawned by a remover, a pallet change or
        // the kill plane while it was in the jaws; a freed node is not
        // something to keep carrying.
        if (!IsInstanceValid(_held)) { _held = null; return; }

        _held.GlobalPosition = _tool.GlobalPosition - new Vector3(0, _held.Height / 2.0f + 0.02f, 0);
    }

    /// <summary>Drop whatever is held and fold back to the parked pose. Called
    /// on scene reset, so a run does not start with a frozen carton stuck to a
    /// gripper that has since been moved somewhere else.</summary>
    public void ResetArm()
    {
        Release();
        _waistAngle = _waistTarget = 0.0f;
        _shoulderAngle = _shoulderTarget = 0.0f;
        _elbowAngle = _elbowTarget = 90.0f;
        _clamped = false;
        ApplyPose();
        ApplyGripVisual(false);
    }

    // ---------- IPart

    public void DeclareTags(PartTagBuilder tags) => tags
        .Float("waist", $"Arm {tags.Index} Waist Cmd (deg)", TagKind.Output)
        .Float("shoulder", $"Arm {tags.Index} Shoulder Cmd (deg)", TagKind.Output)
        .Float("elbow", $"Arm {tags.Index} Elbow Cmd (deg)", TagKind.Output, initial: 90.0)
        .Bit("grip", $"Arm {tags.Index} Gripper", TagKind.Output)
        .Float("atwaist", $"Arm {tags.Index} Waist (deg)", TagKind.Input)
        .Float("atshoulder", $"Arm {tags.Index} Shoulder (deg)", TagKind.Input)
        .Float("atelbow", $"Arm {tags.Index} Elbow (deg)", TagKind.Input, initial: 90.0)
        .Float("reach", $"Arm {tags.Index} Tool Reach (m)", TagKind.Input)
        .Float("height", $"Arm {tags.Index} Tool Height (m)", TagKind.Input)
        .Float("stretch", $"Arm {tags.Index} Stretch (%)", TagKind.Input)
        .Bit("inposition", $"Arm {tags.Index} In Position", TagKind.Input, initial: true)
        .Bit("limit", $"Arm {tags.Index} Command Beyond Limit", TagKind.Input)
        .Bit("holding", $"Arm {tags.Index} Holding", TagKind.Input)
        .Bit("fault", $"Arm {tags.Index} Drive Fault", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("upper_arm", UpperArm);
        settings.Put("forearm", Forearm);
        settings.Put("shoulder_height", ShoulderHeight);
        settings.Put("joint_speed", JointSpeed);
        settings.Put("tolerance", PositionTolerance);
        settings.Put("waist_limit", WaistLimit);
        settings.Put("shoulder_min", ShoulderMin);
        settings.Put("shoulder_max", ShoulderMax);
        settings.Put("elbow_max", ElbowMax);
    }

    public void ApplySettings(PartSettings settings)
    {
        // Clamped on the way in as well as in _Ready: a scene file is editable
        // by hand, and a zero-length link from one would otherwise reach
        // Stretch before _Ready ever ran.
        if (settings.Number("upper_arm") is { } upper) UpperArm = Mathf.Max(MinLink, upper);
        if (settings.Number("forearm") is { } fore) Forearm = Mathf.Max(MinLink, fore);
        if (settings.Number("shoulder_height") is { } height) ShoulderHeight = height;
        if (settings.Number("joint_speed") is { } speed) JointSpeed = speed;
        if (settings.Number("tolerance") is { } tolerance) PositionTolerance = tolerance;
        if (settings.Number("waist_limit") is { } waist) WaistLimit = waist;
        if (settings.Number("shoulder_min") is { } min) ShoulderMin = min;
        if (settings.Number("shoulder_max") is { } max) ShoulderMax = max;
        if (settings.Number("elbow_max") is { } elbow) ElbowMax = elbow;
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);

        Step(tick.Number("waist"), tick.Number("shoulder"), tick.Number("elbow", 90.0f),
             tick.Bit("grip"), tick.Dt);

        tick.Write("atwaist", (double)_waistAngle);
        tick.Write("atshoulder", (double)_shoulderAngle);
        tick.Write("atelbow", (double)_elbowAngle);
        tick.Write("reach", (double)Reach);
        tick.Write("height", (double)ToolHeight);
        tick.Write("stretch", (double)Stretch);
        tick.Write("inposition", InPosition);
        tick.Write("limit", IsAtLimit);
        tick.Write("holding", IsHolding);
    }

    /// <summary>Link lengths and the shoulder height are build-time geometry,
    /// so they are deliberately absent here rather than offered and silently
    /// ignored — the same call <see cref="TurnTable"/> makes about its deck
    /// radius. The stops are not: they are clamped every tick, so moving one
    /// reaches the machine immediately.</summary>
    public void DescribeControls(IPartInspector ui)
    {
        ui.Slider("Joint Speed (deg/s)", JointSpeed, 5.0f, 180.0f, 5.0f, value => JointSpeed = value);
        ui.Slider("In-Position (deg)", PositionTolerance, 0.2f, 10.0f, 0.1f,
                  value => PositionTolerance = value);
        ui.Slider("Waist Limit (deg)", WaistLimit, 10.0f, 180.0f, 5.0f, value => WaistLimit = value);
        ui.Slider("Shoulder Max (deg)", ShoulderMax, 0.0f, 150.0f, 5.0f, value => ShoulderMax = value);
        ui.Slider("Elbow Max (deg)", ElbowMax, 15.0f, 170.0f, 5.0f, value => ElbowMax = value);
    }

    public void ResetPart(PartReset reset)
    {
        ResetArm();
        reset.Write("atwaist", 0.0);
        reset.Write("atshoulder", 0.0);
        reset.Write("atelbow", 90.0);
        reset.Write("holding", false);
        reset.Write("limit", false);
    }

    public PartOperation? Operation => new("arm", "grip");

    public void Operate(PartOperate op) => op.ToggleBit("grip");
}
