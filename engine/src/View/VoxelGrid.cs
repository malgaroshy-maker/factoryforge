using System;
using Godot;

namespace FactoryForge.View;

/// <summary>
/// Discrete 3D voxel coordinate representing grid positions in the factory space.
/// </summary>
public readonly struct VoxelCoord : IEquatable<VoxelCoord>
{
    public int X { get; }
    public int Y { get; }
    public int Z { get; }

    public VoxelCoord(int x, int y, int z)
    {
        X = x;
        Y = y;
        Z = z;
    }

    public bool Equals(VoxelCoord other) => X == other.X && Y == other.Y && Z == other.Z;
    public override bool Equals(object? obj) => obj is VoxelCoord other && Equals(other);
    public override int GetHashCode() => HashCode.Combine(X, Y, Z);
    public override string ToString() => $"VoxelCoord({X}, {Y}, {Z})";

    public static bool operator ==(VoxelCoord left, VoxelCoord right) => left.Equals(right);
    public static bool operator !=(VoxelCoord left, VoxelCoord right) => !left.Equals(right);
}

/// <summary>
/// Manages factory floor voxel grid alignment, snapping, and grid rendering.
/// </summary>
public partial class VoxelGrid : Node3D
{
    [Export] public float CellSize { get; set; } = 0.5f;
    [Export] public int GridExtentX { get; set; } = 10;
    [Export] public int GridExtentZ { get; set; } = 10;
    [Export] public bool ShowGridLines { get; set; } = true;

    private MeshInstance3D? _gridMeshInstance;

    public override void _Ready()
    {
        if (ShowGridLines)
        {
            BuildGridMesh();
            BuildReferenceCube();
        }
    }

    /// <summary>
    /// A 1m cube sitting in the back corner of the build volume, purely for
    /// scale calibration while authoring a part — the numbers in
    /// <see cref="Parts.PartLayout"/> are sourced from real equipment, but a
    /// mesh built to the wrong scale still just looks like a mesh until it is
    /// standing next to something of known size. See FF-25.
    /// </summary>
    private void BuildReferenceCube()
    {
        float x = GridExtentX * CellSize - 0.5f;
        float z = GridExtentZ * CellSize - 0.5f;
        AddChild(new MeshInstance3D
        {
            Name = "ReferenceCube",
            Mesh = new BoxMesh { Size = Vector3.One },
            Position = new Vector3(-x, 0.5f, -z),
            MaterialOverride = new StandardMaterial3D
            {
                AlbedoColor = new Color(0.6f, 0.75f, 1.0f, 0.25f),
                Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
                ShadingMode = BaseMaterial3D.ShadingModeEnum.Unshaded,
            },
        });
    }

    /// <summary>
    /// Draw the grid at a raised floor rather than on the ground (IP-15): the
    /// top of the deck parts on the editor's active level stand on. The grid is
    /// what says where a part will land, and a grid left on the ground while
    /// parts go down a metre above it says the wrong thing. Only the lines
    /// move; the reference cube stays on the ground it measures from.
    /// </summary>
    public void ShowFloorAt(float floorY)
    {
        if (_gridMeshInstance is null) return;
        _gridMeshInstance.Position = new Vector3(0, floorY, 0);
    }

    public VoxelCoord WorldToVoxel(Vector3 worldPos)
    {
        int x = Mathf.RoundToInt(worldPos.X / CellSize);
        int y = Mathf.RoundToInt(worldPos.Y / CellSize);
        int z = Mathf.RoundToInt(worldPos.Z / CellSize);
        return new VoxelCoord(x, y, z);
    }

    public Vector3 VoxelToWorld(VoxelCoord coord)
    {
        return new Vector3(
            coord.X * CellSize,
            coord.Y * CellSize,
            coord.Z * CellSize
        );
    }

    public Vector3 SnapToGrid(Vector3 worldPos)
    {
        return VoxelToWorld(WorldToVoxel(worldPos));
    }

    private void BuildGridMesh()
    {
        var immediate = new ImmediateMesh();
        var material = new StandardMaterial3D
        {
            ShadingMode = BaseMaterial3D.ShadingModeEnum.Unshaded,
            AlbedoColor = new Color(0.4f, 0.45f, 0.5f, 0.35f),
            Transparency = BaseMaterial3D.TransparencyEnum.Alpha,
        };

        immediate.SurfaceBegin(Mesh.PrimitiveType.Lines, material);

        float minX = -GridExtentX * CellSize;
        float maxX = GridExtentX * CellSize;
        float minZ = -GridExtentZ * CellSize;
        float maxZ = GridExtentZ * CellSize;
        float y = 0.005f; // slightly above floor to prevent z-fighting

        for (int x = -GridExtentX; x <= GridExtentX; x++)
        {
            float posX = x * CellSize;
            immediate.SurfaceAddVertex(new Vector3(posX, y, minZ));
            immediate.SurfaceAddVertex(new Vector3(posX, y, maxZ));
        }

        for (int z = -GridExtentZ; z <= GridExtentZ; z++)
        {
            float posZ = z * CellSize;
            immediate.SurfaceAddVertex(new Vector3(minX, y, posZ));
            immediate.SurfaceAddVertex(new Vector3(maxX, y, posZ));
        }

        immediate.SurfaceEnd();

        _gridMeshInstance = new MeshInstance3D
        {
            Name = "GridLines",
            Mesh = immediate,
        };
        AddChild(_gridMeshInstance);
    }
}
