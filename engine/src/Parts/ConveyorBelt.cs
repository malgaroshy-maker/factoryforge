using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Conveyor belt implemented using a surface velocity constraint (ConstantLinearVelocity)
/// on a StaticBody3D with animated tread surface texture scrolling.
/// </summary>
public partial class ConveyorBelt : StaticBody3D, IPart
{
    [Export] public float Speed { get; set; } = 0.5f;
    [Export] public Vector3 Direction { get; set; } = Vector3.Right;
    [Export] public Vector3 Size { get; set; } = new(3.0f, 0.12f, 0.5f);

    /// <summary>
    /// Rubber belt against cardboard. High enough to accelerate a carton up to
    /// line speed without slip, low enough that a blocked box scuffs along
    /// instead of being welded to the surface. Applied live, so changing it in
    /// the inspector re-grips the belt immediately.
    /// </summary>
    [Export]
    public float SurfaceFriction
    {
        get => _surfaceFriction;
        set
        {
            _surfaceFriction = value;
            if (PhysicsMaterialOverride is { } material) material.Friction = value;
        }
    }

    private float _surfaceFriction = 0.70f;

    private CollisionShape3D _collisionShape = null!;
    private StandardMaterial3D? _beltMaterial;

    public bool IsRunning { get; private set; }

    /// <summary>
    /// The drive's own fault contact. A faulted drive does not turn, whatever
    /// the controller commands — which is the entire point of it (FI-01).
    ///
    /// Until this existed, every actuator in the library did exactly what it
    /// was told, so a command and reality could never disagree. That made half
    /// of real PLC work unteachable here: an interlock exists because the plant
    /// does not always obey, and a student who has only ever driven a line that
    /// always obeys has never had to check.
    /// </summary>
    public bool IsFaulted { get; private set; }

    private MeshInstance3D? _faultLamp;
    private StandardMaterial3D? _faultLampMat;

    private bool _hasAppliedVelocity;
    private bool _lastRunning;
    private Basis _lastBasis;
    private float _lastSpeed;

    /// <summary>The head and tail drums, so they can be turned at the belt's
    /// own surface speed (CP-10). Found by name once rather than searched every
    /// frame — and by name rather than "the cylinders", because the drive-fault
    /// beacon is mounted on a cylindrical stalk and a positional guess is how
    /// the roller deck's own test ended up measuring a lamp post.</summary>
    private readonly System.Collections.Generic.List<MeshInstance3D> _drums = new();

    /// <summary>Drum radius, from the geometry the builder actually made, so a
    /// resized belt's drums still turn at the right rate.</summary>
    private float _drumRadius = 0.07f;

    private float _drumSpin;

    public override void _Ready()
    {
        var visual = IndustrialMeshBuilder.BuildDetailedConveyor(Size, out _beltMaterial);
        AddChild(visual);

        _drumRadius = Size.Y / 2.0f + 0.008f;
        foreach (string name in new[] { "HeadDrum", "TailDrum" })
        {
            if (visual.GetNodeOrNull<MeshInstance3D>(name) is { } drum) _drums.Add(drum);
        }

        _collisionShape = new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = Size }
        };
        AddChild(_collisionShape);

        AddTransferRamps();

        PhysicsMaterialOverride = new PhysicsMaterial
        {
            Friction = _surfaceFriction,
            Bounce = 0.0f,
            Rough = true,
        };

        BuildFaultLamp();
    }

    /// <summary>Reach of the lead-in wedge beyond each end of the deck.</summary>
    private const float RampRun = 0.10f;

    /// <summary>How far below deck level the outer lip of the wedge sits.
    /// Comfortably more than any contact slop a carton can accumulate.</summary>
    private const float RampRise = 0.05f;

    private const float RampThickness = 0.04f;

    /// <summary>
    /// A shallow wedge at each end of the deck, sloping from below deck level
    /// up to it.
    ///
    /// This exists because of the most obvious thing anybody builds — two
    /// conveyors in a line — and a fact about rigid-body contact that is not a
    /// bug and cannot be tuned away: a body resting on a static deck settles a
    /// centimetre or two *inside* it. That is the solver's contact slop. A
    /// carton riding two centimetres low then meets the vertical end face of
    /// the next conveyor's collider head-on, wedges against it, and stops the
    /// whole queue behind it — on a belt that is visibly running. It happened
    /// on roughly half of the pick-and-place cell's runs, and every one of them
    /// looked like a scene-design problem rather than an engine one.
    ///
    /// The wedge removes the face: a carton arriving low rides up it instead of
    /// into it. It reaches past the deck end, so adjacent decks overlap it, and
    /// its outer lip is below deck level, so a carton travelling at the correct
    /// height never touches it at all.
    ///
    /// Collision only — <see cref="Editor.PartBounds"/> measures meshes, so the
    /// ramps do not grow the part's selection box or its footprint in the
    /// editor.
    /// </summary>
    private void AddTransferRamps()
    {
        float theta = Mathf.Atan2(RampRise, RampRun);
        float halfLength = RampRun / 2.0f;
        float halfThickness = RampThickness / 2.0f;

        // Offset from the wedge's centre to its upper, deck-side corner. Pin
        // that corner to (end of deck, top of deck) and the slope follows.
        float dx = halfLength * Mathf.Cos(theta) - halfThickness * Mathf.Sin(theta);
        float dy = halfLength * Mathf.Sin(theta) + halfThickness * Mathf.Cos(theta);

        float deckTop = Size.Y / 2.0f;
        float deckEnd = Size.X / 2.0f;

        foreach (int end in new[] { -1, 1 })
        {
            var ramp = new CollisionShape3D
            {
                Name = end < 0 ? "TailTransferRamp" : "HeadTransferRamp",
                Shape = new BoxShape3D { Size = new Vector3(RampRun, RampThickness, Size.Z) },
                Position = new Vector3(end * (deckEnd + dx), deckTop - dy, 0),
            };
            // Rotate so the deck-side end is the high one at both ends: the
            // belt may be reversed or rotated in the editor, and a ramp that
            // only worked in one direction would be half a fix.
            ramp.RotateZ(-end * theta);
            AddChild(ramp);
        }
    }

    /// <summary>A beacon on the drive end, dark until the drive faults. On a
    /// real line this is the light on the motor starter, and it is there for
    /// the same reason it is here: a stopped belt looks identical to a faulted
    /// one, and the difference is the whole diagnosis.</summary>
    private void BuildFaultLamp()
    {
        _faultLampMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.30f, 0.06f, 0.06f),
            Metallic = 0.10f,
            Roughness = 0.35f,
        };
        // On the side of the head end, clear of the belt surface so a carton
        // never hides it, on a short stalk so it reads as mounted to the
        // frame rather than floating beside it.
        var mount = new Vector3(Size.X / 2 - 0.08f, Size.Y / 2 + 0.02f, Size.Z / 2 + 0.05f);
        const float stalk = 0.07f;

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

        _faultLamp = new MeshInstance3D
        {
            Name = "DriveFaultLamp",
            Mesh = new SphereMesh { Radius = 0.042f, Height = 0.084f },
            MaterialOverride = _faultLampMat,
            Position = mount + new Vector3(0, stalk + 0.028f, 0),
        };
        AddChild(_faultLamp);
    }

    /// <summary>Raise or clear the drive fault. Nothing in the simulation sets
    /// this — it is an Input, driven by whoever is playing maintenance: the
    /// toolbar's fault tool, a forced tag, or a test.</summary>
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

    public override void _Process(double delta)
    {
        if (!IsRunning) return;
        float dt = (float)delta;

        if (_beltMaterial is not null)
        {
            Vector3 offset = _beltMaterial.Uv1Offset;
            offset.X += Speed * dt * 0.8f;
            _beltMaterial.Uv1Offset = offset;
        }

        // Drums turn at the rate the surface actually moves, so the belt and
        // what carries it agree instead of the drums being decorative.
        //
        // Composed as a basis rather than as euler angles: Godot builds those
        // as Y*X*Z, so a Z term is applied first, in the mesh's own frame where
        // the cylinder's axis is still +Y — which tips the drum over instead of
        // turning it about itself. That is the bug that made the roller deck
        // tumble end over end, and it would land here identically.
        if (_drums.Count == 0 || _drumRadius <= 0.0f) return;
        _drumSpin += Speed / _drumRadius * dt;
        var lay = new Basis(Vector3.Right, Mathf.Pi / 2);
        var spin = new Basis(Vector3.Up, -_drumSpin);
        foreach (var drum in _drums) drum.Basis = lay * spin;
    }

    /// <summary>
    /// Called every physics tick regardless of whether anything changed, so
    /// this used to redo a matrix multiply and a square root on every belt on
    /// every tick even when the belt was sitting there doing nothing (FF-16).
    /// Skipping when (running, orientation, speed) all match the last applied
    /// values makes the steady-state case free while still catching the case
    /// that made this call unconditional in the first place: rotating a
    /// running belt has to rotate its transport direction with it, and a live
    /// speed-slider edit has to take effect without a rotate to trigger it.
    /// </summary>
    public void SetRunning(bool running)
    {
        // The fault wins over the command. Ordered before everything else so
        // there is exactly one place the two can disagree, and it resolves the
        // same way every time.
        if (IsFaulted) running = false;

        IsRunning = running;
        Basis basis = GlobalBasis;

        if (_hasAppliedVelocity && running == _lastRunning && basis == _lastBasis
            && Mathf.IsEqualApprox(Speed, _lastSpeed))
        {
            return;
        }

        // ConstantLinearVelocity is a world-space vector, but Direction describes
        // the belt's own travel. Rotating a belt in the editor (R) has to rotate
        // the transport with it, or a turned belt still drives boxes down +X.
        Vector3 worldDir = (basis * Direction).Normalized();
        ConstantLinearVelocity = running ? worldDir * Speed : Vector3.Zero;

        _lastRunning = running;
        _lastBasis = basis;
        _lastSpeed = Speed;
        _hasAppliedVelocity = true;
    }

    // ---------- IPart (HP-34)

    /// <summary>Is this belt's speed a setting somebody chose? On a plain belt
    /// it is. On a drive it is not — the VFD recomputes it from the speed
    /// reference every tick, so saving it would store a sample and restore it
    /// as configuration, and offering a slider for it would be a control
    /// overwritten before the next frame. One flag rather than two separate
    /// "is this a VariableConveyor" tests that could disagree.</summary>
    protected virtual bool SpeedIsSetting => true;

    public virtual void DeclareTags(PartTagBuilder tags) => tags
        .Bit("rotate", $"Conveyor {tags.Index} (Rotate)", TagKind.Output)
        // The drive's own fault contact (FI-01). An Input, because nothing in
        // the simulation computes it -- it is raised by whoever is playing
        // maintenance, and read by the controller exactly like a sensor.
        .Bit("fault", $"Conveyor {tags.Index} Drive Fault", TagKind.Input);

    public virtual void CaptureSettings(PartSettings settings)
    {
        if (SpeedIsSetting) settings.Put("speed", Speed);
        settings.Put("friction", SurfaceFriction);
        settings.Put("size", Size);
        // Which way the surface drives. Read by the belt and never recomputed,
        // so it is configuration -- and it was the one belt setting a save did
        // not carry, which meant a belt built to run backwards came back
        // running forwards, in a line whose geometry gave no hint (HP-06).
        settings.Put("dir", Direction);
    }

    public virtual void ApplySettings(PartSettings settings)
    {
        if (settings.Number("speed") is { } speed) Speed = speed;
        if (settings.Number("friction") is { } friction) SurfaceFriction = friction;
        if (settings.Vector("size") is { } size) Size = size;
        if (settings.Vector("dir") is { } direction) Direction = direction;
    }

    public virtual void StepPart(PartTick tick)
    {
        if (!tick.TryBit("rotate", out bool rotate)) return;

        // Fault first, so SetRunning below already knows: a faulted drive
        // refuses the command rather than obeying it and being stopped again
        // next tick.
        if (tick.TryBit("fault", out bool faulted)) SetFaulted(faulted);
        SetRunning(rotate);
        tick.Host.NoteTransportSpeed(tick.InstanceId, Speed);
    }

    public virtual void DescribeControls(IPartInspector ui)
    {
        if (SpeedIsSetting)
            ui.Slider("Belt Speed (m/s)", Speed, 0.05f, 2.0f, 0.05f, value => Speed = value);
        ui.Slider("Surface Friction", SurfaceFriction, 0.05f, 1.5f, 0.05f,
                  value => SurfaceFriction = value);
    }

    public virtual PartOperation? Operation => new("conveyor", "rotate");

    public virtual void Operate(PartOperate op) => op.ToggleBit("rotate");
}
