using System.Collections.Generic;
using FactoryForge.Editor;
using Godot;

namespace FactoryForge.Sim;

/// <summary>
/// Check the engine can find a sidecar to launch (UX-04).
///
/// <code>godot --headless --path engine -- --self-test=sidecar</code>
///
/// Worth its own check because the failure is invisible from inside the
/// engine: F5 falls back to "command copied, run it yourself", which looks
/// like a deliberate feature rather than a search that missed. In a packaged
/// build that fallback used to be the *only* path, because
/// <c>res://</c> resolves to the executable's own directory in an export and
/// the old code looked in its parent.
///
/// Run this against a release binary as well as a checkout -- it reports which
/// kind it found, and the two answers should differ: Source in a checkout,
/// Bundled beside a shipped binary.
/// </summary>
public partial class SidecarLocatorSelfTest : Node
{
    private readonly List<string> _failures = new();

    private void Expect(bool condition, string what)
    {
        if (condition) return;
        _failures.Add(what);
        GD.PrintErr($"  FAIL  {what}");
    }

    public override void _Ready()
    {
        GD.Print("  searched, in order:");
        foreach (string dir in SidecarLocator.SearchDirs())
            GD.Print($"    {(dir.Length == 0 ? "(unset)" : dir)}");

        var found = SidecarLocator.Find();
        Expect(found is not null,
               "a sidecar was found -- without one, F5 can only copy a command to the clipboard");

        if (found is { } sidecar)
        {
            GD.Print($"  found: {sidecar.Kind} at {sidecar.Path}");
            GD.Print($"  would run: cd \"{sidecar.WorkingDir}\" && {sidecar.CommandFor("connect --driver mock")}");

            Expect(sidecar.WorkingDir.Length > 0, "the located sidecar has a working directory");
            Expect(sidecar.CommandFor("connect").Contains("connect"),
                   "the command carries the sidecar's own arguments");
            // A frozen build must not be prefixed with an interpreter, and a
            // source checkout must be: getting this backwards produces a
            // command that looks plausible and cannot run.
            if (sidecar.Kind == SidecarKind.Bundled)
                Expect(!sidecar.CommandFor("connect").StartsWith("python"),
                       "a bundled sidecar runs itself, with no interpreter in front");
            else
                Expect(sidecar.CommandFor("connect").StartsWith("python"),
                       "a source sidecar is run through an interpreter");
        }

        try { CheckModbusBind(); }
        catch (System.Exception e) { Expect(false, $"modbus bind check threw: {e}"); }

        if (_failures.Count == 0)
        {
            GD.Print("self-test sidecar: PASS");
            GetTree().Quit(0);
            return;
        }
        GD.PrintErr($"self-test sidecar: FAIL ({_failures.Count})");
        GetTree().Quit(1);
    }

    /// <summary>
    /// IP-37: F5's Modbus host and port reach the command it launches and
    /// copies. Goes through the dialog's own instance path -- the property the
    /// form sets, then <c>BuildSidecarArguments()</c> -- because a field that
    /// is validated and stored but never read is the F4/F5 failure of gotcha
    /// 11. The dialog is not added to the tree, so its _Ready never loads the
    /// user's saved settings and the defaults seen here are the shipped ones.
    /// Nothing is launched and nothing binds.
    /// </summary>
    private void CheckModbusBind()
    {
        var ui = new DriverConnectionUI { SelectedDriver = "modbus-tcp" };
        try
        {
            string cmd = ui.BuildSidecarArguments();
            GD.Print($"  modbus default: {cmd}");
            Expect(cmd.Contains("--driver modbus-tcp -o host 127.0.0.1 -o port 502"),
                   $"the default Modbus command binds loopback on 502 (HP-22), got: {cmd}");

            Expect(ui.TryApplyModbusBind(" 0.0.0.0 ", "5502", out string err),
                   $"0.0.0.0:5502 is accepted ({err})");
            cmd = ui.BuildSidecarArguments();
            GD.Print($"  modbus chosen:  {cmd}");
            Expect(cmd.Contains("-o host 0.0.0.0 -o port 5502"),
                   $"a chosen host and port reach the command, got: {cmd}");
            Expect(!cmd.Contains("127.0.0.1") && !cmd.Contains(" 502"),
                   $"the chosen bind replaces the default rather than adding to it, got: {cmd}");

            Expect(ui.TryApplyModbusBind("172.28.80.1", "502", out err),
                   $"an interface address is accepted ({err})");
            Expect(ui.TryApplyModbusBind("wsl-host.local", "1", out err),
                   $"a host name and port 1 are accepted ({err})");
            Expect(ui.TryApplyModbusBind("::1", "65535", out err),
                   $"IPv6 loopback and port 65535 are accepted ({err})");

            Expect(ui.TryApplyModbusBind("0.0.0.0", "5502", out _), "reset to a known bind");
            foreach (var (host, port) in new[]
                     {
                         ("127.0.0.1", "0"), ("127.0.0.1", "65536"), ("127.0.0.1", "abc"),
                         ("127.0.0.1", ""), ("127.0.0.1", "-1"),
                         ("", "502"), ("not a host", "502"), ("127.0.0.1:502", "502"),
                         ("opc.tcp://x", "502"),
                     })
            {
                bool ok = ui.TryApplyModbusBind(host, port, out err);
                Expect(!ok, $"'{host}' port '{port}' is refused");
                Expect(ok || err.Length > 0, $"refusing '{host}' '{port}' says why");
            }
            // A refused form changes nothing -- in particular a good host with
            // a bad port must not take the host.
            Expect(!ui.TryApplyModbusBind("10.0.0.5", "70000", out _), "bad port refused");
            cmd = ui.BuildSidecarArguments();
            Expect(cmd.Contains("-o host 0.0.0.0 -o port 5502"),
                   $"a refused bind leaves the last good one in place, got: {cmd}");

            // The bind belongs to the Modbus driver only.
            ui.SelectedDriver = "opcua-server";
            cmd = ui.BuildSidecarArguments();
            Expect(!cmd.Contains("-o host") && !cmd.Contains("-o port"),
                   $"a non-Modbus server gets no Modbus bind, got: {cmd}");
        }
        finally
        {
            ui.Free();
        }
    }
}
