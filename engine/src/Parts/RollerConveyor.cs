using System.Collections.Generic;
using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Driven roller conveyor. Transport works exactly as the belt does — a surface
/// velocity constraint — but the deck is a row of turning rollers instead of a
/// continuous band, which is what you actually use for pallets and totes that
/// would scuff a belt.
///
/// Inherits <see cref="ConveyorBelt"/> so it shares the speed, friction and
/// rotation handling, and so the editor drives it through the same tag.
/// </summary>
public partial class RollerConveyor : ConveyorBelt
{
    [Export] public float RollerSpacing { get; set; } = 0.12f;

    private const float RollerRadius = 0.035f;

    private readonly List<MeshInstance3D> _rollers = new();
    private float _spin;

    public override void _Ready()
    {
        base._Ready();

        // The inherited visual is a continuous band; hide it and lay rollers.
        // The head and tail drums go with it (CP-10) — they are what a belt
        // runs on, and a roller deck has neither.
        var visual = GetNodeOrNull<Node3D>("ConveyorVisual");
        var band = visual?.GetNodeOrNull<MeshInstance3D>("BeltSurfaceMesh");
        if (band is not null) band.Visible = false;
        foreach (string drumName in new[] { "HeadDrum", "TailDrum" })
        {
            if (visual?.GetNodeOrNull<MeshInstance3D>(drumName) is { } drum) drum.Visible = false;
        }

        var rollerMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.68f, 0.70f, 0.74f),
            Metallic = 0.65f,
            Roughness = 0.30f,
        };

        var seamMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.24f, 0.25f, 0.28f),
            Metallic = 0.40f,
            Roughness = 0.55f,
        };

        int count = Mathf.Max((int)(Size.X / RollerSpacing), 2);
        float span = Size.Z - 0.08f;

        for (int i = 0; i < count; i++)
        {
            float x = -Size.X / 2 + RollerSpacing * (i + 0.5f);
            if (x > Size.X / 2) break;

            var roller = new MeshInstance3D
            {
                // Named, so anything looking for a roller can ask for one
                // rather than guessing "the first cylinder child". That guess
                // was in RollerProfileSelfTest until a drive-fault beacon --
                // mounted on a cylindrical stalk by the base conveyor -- became
                // the first cylinder and the test started measuring a lamp
                // post for rotation.
                Name = $"Roller{i}",
                Mesh = new CylinderMesh
                {
                    TopRadius = RollerRadius,
                    BottomRadius = RollerRadius,
                    Height = span,
                },
                MaterialOverride = rollerMat,
                Position = new Vector3(x, Size.Y / 2 - 0.015f, 0),
            };
            // Cylinder axis across the lane, so it rolls the right way.
            roller.Rotation = new Vector3(Mathf.Pi / 2, 0, 0);

            // A plain cylinder turning about its own axis looks identical at
            // every angle, so a correct roll would read as no motion at all.
            // A seam down the rim is what makes a real roller deck look like it
            // is running. In the roller's own frame the cylinder still runs
            // along +Y, so the seam is long in Y and offset by the radius.
            roller.AddChild(new MeshInstance3D
            {
                Mesh = new BoxMesh { Size = new Vector3(0.007f, span * 0.98f, 0.007f) },
                MaterialOverride = seamMat,
                Position = new Vector3(RollerRadius, 0, 0),
            });

            AddChild(roller);
            _rollers.Add(roller);
        }
    }

    public override void _Process(double delta)
    {
        base._Process(delta);
        if (!IsRunning) return;

        // Spin at the rate the surface actually moves, so the rollers and the
        // load agree rather than the rollers being decorative.
        _spin += Speed / RollerRadius * (float)delta;

        // Composed as a basis, not as a Z euler alongside the X lay-down.
        // Godot builds euler angles as Y*X*Z, so a Z term is applied *first*,
        // in the mesh's own frame — where the cylinder's axis is still +Y, so
        // it tips the roller over instead of turning it about itself. That made
        // the whole deck tumble end over end: measured at 150 degrees of axis
        // drift over one run, exactly matching the rim's own travel.
        //
        // Lay the cylinder down (its +Y axis onto the lane's +Z), then spin
        // about that same axis. Negative, because the deck carries +X: for a
        // rim point at the top, w x r = v needs w along -Z.
        var lay = new Basis(Vector3.Right, Mathf.Pi / 2);
        var spin = new Basis(Vector3.Up, -_spin);
        foreach (var roller in _rollers)
        {
            roller.Basis = lay * spin;
        }
    }

    // ---------- IPart (HP-34)

    public override void DeclareTags(PartTagBuilder tags) => tags
        .Bit("rotate", $"Roller Conveyor {tags.Index} (Rotate)", TagKind.Output)
        .Bit("fault", $"Roller Conveyor {tags.Index} Drive Fault", TagKind.Input);

    public override void CaptureSettings(PartSettings settings)
    {
        base.CaptureSettings(settings);
        // The setting that makes it a *roller* bed rather than a belt. The
        // roller geometry is rebuilt from it in _Ready, so losing it changed
        // what the part looked like as well as how it behaved.
        settings.Put("roller_spacing", RollerSpacing);
    }

    public override void ApplySettings(PartSettings settings)
    {
        base.ApplySettings(settings);
        if (settings.Number("roller_spacing") is { } spacing) RollerSpacing = spacing;
    }

    public override PartOperation? Operation => new("roller conveyor", "rotate");
}
