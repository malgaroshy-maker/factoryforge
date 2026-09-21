using FactoryForge.TagBus;
using Godot;

namespace FactoryForge.Parts;

/// <summary>
/// Area3D zone that removes boxes when they reach the end of the conveyor or chute.
/// </summary>
public partial class Remover : Area3D, IPart
{
    [Export] public Vector3 ZoneSize { get; set; } = new(0.40f, 0.40f, 0.60f);

    /// <summary>
    /// Tag this remover counts into. Empty means the conventional
    /// "&lt;instanceId&gt;.count"; the sorting scene points its two removers at
    /// counter.tall and counter.short so the same PLC program reads the same
    /// tags whichever scene is running.
    /// </summary>
    [Export] public string CountTag { get; set; } = "";

    private CollisionShape3D _collisionShape = null!;

    /// <summary>Boxes despawned since the scene started.</summary>
    public int RemovedCount { get; private set; }

    public void ResetCount() => RemovedCount = 0;

    public override void _Ready()
    {
        _collisionShape = new CollisionShape3D
        {
            Shape = new BoxShape3D { Size = ZoneSize }
        };
        AddChild(_collisionShape);

        BuildMarker();
        BodyEntered += OnBodyEntered;
    }

    /// <summary>
    /// An outfeed gate marking the catch zone. The remover was a bare Area3D
    /// with a collision shape and nothing to look at, so placing it from the
    /// palette appeared to do nothing at all.
    /// </summary>
    private void BuildMarker()
    {
        var frameMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.55f, 0.57f, 0.60f),
            Metallic = 0.35f,
            Roughness = 0.50f,
        };
        var stripeMat = new StandardMaterial3D
        {
            AlbedoColor = new Color(0.90f, 0.35f, 0.10f),
            Roughness = 0.45f,
        };

        float halfZ = ZoneSize.Z / 2;
        float top = ZoneSize.Y / 2;

        foreach (int side in new[] { -1, 1 })
        {
            AddChild(new MeshInstance3D
            {
                Mesh = new BoxMesh { Size = new Vector3(0.05f, ZoneSize.Y, 0.05f) },
                MaterialOverride = frameMat,
                Position = new Vector3(0, 0, side * halfZ),
            });
        }

        AddChild(new MeshInstance3D
        {
            Mesh = new BoxMesh { Size = new Vector3(0.06f, 0.06f, ZoneSize.Z) },
            MaterialOverride = stripeMat,
            Position = new Vector3(0, top, 0),
        });
    }

    private void OnBodyEntered(Node3D body)
    {
        if (body is BoxPhysics box)
        {
            RemovedCount++;
            box.QueueFree();
        }
    }

    // ---------- IPart (HP-34)

    /// <summary>Which tag this remover publishes into: its own unless somebody
    /// pointed it at a shared total.</summary>
    private string CountTagOr(string instanceId) =>
        CountTag.Length > 0 ? CountTag : $"{instanceId}.count";

    public void DeclareTags(PartTagBuilder tags) =>
        tags.Int("count", $"Remover {tags.Index} (Count)", TagKind.Input);

    public void CaptureSettings(PartSettings settings)
    {
        // External: a wire to a tag elsewhere, not a property of this machine,
        // so a copy must not carry it (HP-16). Duplicating a remover that
        // counted into `counter.tall` gave two removers writing one counter.
        settings.PutExternal("count_tag", CountTag);
        settings.Put("zone", ZoneSize);
    }

    public void ApplySettings(PartSettings settings)
    {
        if (settings.Text("count_tag") is { } tag) CountTag = tag;
        if (settings.Vector("zone") is { } zone) ZoneSize = zone;
    }

    public void StepPart(PartTick tick) =>
        tick.WriteTo(CountTagOr(tick.InstanceId), RemovedCount);

    public void DescribeControls(IPartInspector ui)
    {
        string own = $"{ui.InstanceId}.count";
        ui.TagPicker("Counts into", CountTagOr(ui.InstanceId), own, TagType.Int, TagKind.Input,
                     chosen => CountTag = chosen == own ? "" : chosen);
    }

    /// <summary>A remover pointed at its own count tag holds that tag's full
    /// id, so a rename has to carry it. One pointed at a shared total is
    /// pointing somewhere else and is left alone.</summary>
    public void PrefixRenamed(string oldId, string newId)
    {
        if (CountTag.StartsWith(oldId + ".")) CountTag = newId + CountTag[oldId.Length..];
    }

    public void ResetPart(PartReset reset)
    {
        ResetCount();
        reset.WriteTo(CountTagOr(reset.InstanceId), 0);
    }
}
