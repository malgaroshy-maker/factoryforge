/// <summary>Who made this, in one place so the start screen, the key overlay
/// and the window title cannot drift apart.</summary>
public static class Credits
{
    public const string Author = "Mahamed Algaroshy";
    public const string AuthorAr = "محمد الجروشي";

    /// <summary>Shown in the UI; Godot's TextServerAdvanced shapes the Arabic.</summary>
    public const string Line = "Developed by " + Author + " · " + AuthorAr;

    /// <summary>The window title bar is drawn by the OS, so keep it Latin.</summary>
    public const string WindowTitle = "FactoryForge — by " + Author;
}
