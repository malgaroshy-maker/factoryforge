using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Rotating beacon and sounder (CP-06).
///
/// The stack light says what state the line is in to somebody standing at it.
/// A beacon says something has gone wrong to somebody at the other end of the
/// building, and it does that by throwing light around rather than by being
/// bright. So this one carries a real <see cref="SpotLight3D"/> that sweeps: it
/// lights the parts near it, and you notice it in peripheral vision, which is
/// the entire design intent of the real thing.
///
/// The horn is shown, not sounded. Audio is a dependency that fights
/// `--headless` runs and CI, and this project does not otherwise carry one.
/// </summary>
public partial class AlarmBeacon : Node3D, IPart
{
    /// <summary>Beacon revolutions per second.</summary>
    [Export] public float RotationSpeed { get; set; } = 1.6f;

    [Export] public Color BeaconColour { get; set; } = new(1.0f, 0.55f, 0.05f);

    private const float DomeY = 0.72f;
    private const float DomeRadius = 0.085f;

    public bool BeaconOn { get; private set; }
    public bool HornOn { get; private set; }

    private Node3D _rotor = null!;
    private SpotLight3D _sweep = null!;
    private OmniLight3D _glow = null!;
    private StandardMaterial3D _domeMat = null!;
    private StandardMaterial3D _hornMat = null!;
    private MeshInstance3D _diaphragm = null!;

    private float _spin;
    private float _hornPhase;

    public override void _Ready()
    {
        var postMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.22f, 0.23f, 0.26f),
            Metallic = 0.50f,
            Roughness = 0.40f,
        };

        float postHeight = PartLayout.FloorDrop + DomeY - 0.10f;
        AddChild(new MeshInstance3D
        {
            Name = "Post",
            Mesh = new CylinderMesh { TopRadius = 0.028f, BottomRadius = 0.032f, Height = postHeight },
            MaterialOverride = postMat,
            Position = new Vector3(0, DomeY - 0.10f - postHeight / 2.0f, 0),
        });
        AddChild(new MeshInstance3D
        {
            Name = "BaseFoot",
            Mesh = new CylinderMesh { TopRadius = 0.10f, BottomRadius = 0.10f, Height = 0.025f },
            MaterialOverride = postMat,
            Position = new Vector3(0, -PartLayout.FloorDrop, 0),
        });

        // Horn, below the dome, facing along +Z.
        _hornMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.18f, 0.19f, 0.21f),
            Metallic = 0.35f,
            Roughness = 0.50f,
        };
        var horn = new MeshInstance3D
        {
            Name = "Horn",
            Mesh = new CylinderMesh { TopRadius = 0.075f, BottomRadius = 0.032f, Height = 0.10f },
            MaterialOverride = _hornMat,
            Position = new Vector3(0, DomeY - 0.20f, 0.05f),
        };
        horn.RotateX(Mathf.Pi / 2);
        AddChild(horn);

        // A diaphragm that visibly beats while the horn is on. A sounder that
        // only changes colour reads as a second lamp.
        _diaphragm = new MeshInstance3D
        {
            Name = "HornDiaphragm",
            Mesh = new CylinderMesh { TopRadius = 0.062f, BottomRadius = 0.062f, Height = 0.008f },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.40f, 0.10f, 0.08f),
                Roughness = 0.6f,
            },
            Position = new Vector3(0, DomeY - 0.20f, 0.098f),
        };
        _diaphragm.RotateX(Mathf.Pi / 2);
        AddChild(_diaphragm);

        // Base of the beacon, then the rotor inside, then the dome over both.
        AddChild(new MeshInstance3D
        {
            Name = "BeaconBase",
            Mesh = new CylinderMesh { TopRadius = DomeRadius, BottomRadius = DomeRadius * 1.05f, Height = 0.045f },
            MaterialOverride = postMat,
            Position = new Vector3(0, DomeY - 0.10f, 0),
        });

        _rotor = new Node3D { Name = "BeaconRotor", Position = new Vector3(0, DomeY - 0.055f, 0) };
        AddChild(_rotor);

        // The mirror the lamp bounces off, so there is something visibly
        // turning behind the dome rather than a light that merely moves.
        _rotor.AddChild(new MeshInstance3D
        {
            Name = "Mirror",
            Mesh = new BoxMesh { Size = new Vector3(0.055f, 0.055f, 0.008f) },
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.90f, 0.90f, 0.94f),
                Metallic = 0.90f,
                Roughness = 0.08f,
            },
            Position = new Vector3(0, 0.02f, 0),
        });

        _sweep = new SpotLight3D
        {
            Name = "BeaconSweep",
            LightColor = BeaconColour,
            LightEnergy = 0.0f,
            SpotRange = 6.0f,
            SpotAngle = 22.0f,
            SpotAngleAttenuation = 0.8f,
            ShadowEnabled = false,
            Position = new Vector3(0, 0.02f, 0),
        };
        // A spot points down -Z by default; tip it up slightly so the beam
        // sweeps across the machines rather than into the floor at its feet.
        _sweep.RotateX(Mathf.DegToRad(8));
        _rotor.AddChild(_sweep);

        _glow = new OmniLight3D
        {
            Name = "BeaconGlow",
            LightColor = BeaconColour,
            LightEnergy = 0.0f,
            OmniRange = 1.6f,
            ShadowEnabled = false,
            Position = new Vector3(0, DomeY - 0.045f, 0),
        };
        AddChild(_glow);

        _domeMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(BeaconColour.R, BeaconColour.G, BeaconColour.B, 0.45f),
            Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
            EmissionEnabled = true,
            Emission = BeaconColour,
            EmissionEnergyMultiplier = 0.1f,
            Roughness = 0.25f,
            CullMode = BaseMaterial3D.CullModeEnum.Disabled,
        };
        AddChild(new MeshInstance3D
        {
            Name = "BeaconDome",
            Mesh = new SphereMesh { Radius = DomeRadius, Height = DomeRadius * 1.7f, IsHemisphere = true },
            MaterialOverride = _domeMat,
            Position = new Vector3(0, DomeY - 0.075f, 0),
        });

        ApplyState();
    }

    public void SetBeacon(bool on)
    {
        if (on == BeaconOn) return;
        BeaconOn = on;
        ApplyState();
    }

    public void SetHorn(bool on)
    {
        if (on == HornOn) return;
        HornOn = on;
        ApplyState();
    }

    private void ApplyState()
    {
        if (_domeMat is null) return;

        _domeMat.EmissionEnergyMultiplier = BeaconOn ? 2.2f : 0.1f;
        if (_sweep is not null) _sweep.LightEnergy = BeaconOn ? 6.0f : 0.0f;
        if (_glow is not null) _glow.LightEnergy = BeaconOn ? 1.1f : 0.0f;
        if (_hornMat is not null)
        {
            _hornMat.EmissionEnabled = HornOn;
            _hornMat.Emission = new Color(1.0f, 0.20f, 0.15f);
            _hornMat.EmissionEnergyMultiplier = HornOn ? 1.2f : 0.0f;
        }
    }

    public override void _Process(double delta)
    {
        if (BeaconOn && _rotor is not null)
        {
            _spin += RotationSpeed * Mathf.Tau * (float)delta;
            _rotor.Rotation = new Vector3(0, _spin, 0);
        }

        if (_diaphragm is null) return;
        if (HornOn)
        {
            // ~4 Hz, the cadence of an industrial sounder.
            _hornPhase += (float)delta * Mathf.Tau * 4.0f;
            float beat = 1.0f + Mathf.Sin(_hornPhase) * 0.18f;
            _diaphragm.Scale = new Vector3(beat, 1.0f, beat);
        }
        else if (_diaphragm.Scale != Vector3.One)
        {
            _diaphragm.Scale = Vector3.One;
        }
    }

    // ---------- IPart (HP-34)

    public void DeclareTags(PartTagBuilder tags) => tags
        .Bit("beacon", $"Beacon {tags.Index} Light", TagKind.Output)
        .Bit("horn", $"Beacon {tags.Index} Horn", TagKind.Output);

    public void CaptureSettings(PartSettings settings)
    {
        settings.Put("rotation_speed", RotationSpeed);
        settings.Put("colour_r", BeaconColour.R);
        settings.Put("colour_g", BeaconColour.G);
        settings.Put("colour_b", BeaconColour.B);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Number("rotation_speed") is { } spin) RotationSpeed = spin;
        if (settings.Number("colour_r") is { } r && settings.Number("colour_g") is { } g
                                                 && settings.Number("colour_b") is { } b)
            BeaconColour = new Color(r, g, b);
    }

    public void StepPart(PartTick tick)
    {
        if (tick.TryBit("beacon", out bool beacon)) SetBeacon(beacon);
        if (tick.TryBit("horn", out bool horn)) SetHorn(horn);
    }

    public void DescribeControls(IPartInspector ui) =>
        ui.Slider("Rotation (rev/s)", RotationSpeed, 0.2f, 5.0f, 0.1f,
                  value => RotationSpeed = value);

    public PartOperation? Operation => new("beacon", "beacon");

    public void Operate(PartOperate op) => op.ToggleBit("beacon");
}
