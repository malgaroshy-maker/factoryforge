using System.Text.Json.Nodes;
using Godot;
using FactoryForge.Editor;
using FactoryForge.Scenes;
using FactoryForge.Sim;
using FactoryForge.TagBus;
using FactoryForge.View;

namespace FactoryForge;

/// <summary>
/// Engine entry point: owns the scene and the tag bus, and drives the fixed
/// timestep. Headless-friendly so CI can run it without a GPU.
/// </summary>
public partial class Main : Node
{
    /// <summary>Cap on catch-up steps per frame. Beyond this the backlog is
    /// dropped rather than spiralling: a simulation that can never keep up
    /// should run visibly slow, not freeze.</summary>
    private const int MaxCatchUpSteps = 5;

    private SortingScene? _scene;
    private SceneEditor? _editor;
    private PartPropertyInspectorUI? _propertyInspector;
    private SimulationControls _sim = null!;
    private bool _deterministic;
    private float _timeScale = 1.0f;

    /// <summary>Both scenes present the same tag interface, so they report the
    /// same name on the bus — a driver cannot and need not tell them apart.</summary>
    private const string SceneName = "sorting-by-height";
    private TagBusServer _bus = null!;
    private double _accumulator;
    private double _runFor = -1;   // seconds; -1 = forever
    private double _elapsed;
    private double _startedAt;
    private string? _screenshotPath;
    private double _screenshotAt = -1;
    private string? _selfTest;

    /// <summary>Scene to open instead of the sorting demo. Skips the start
    /// screen, which is what you want when scripting a run or screenshotting a
    /// particular line.</summary>
    private string? _scenePath;

    /// <summary>Skip the start screen and start the built-in demo driver
    /// immediately, for scripting a screenshot or a recording of "Watch it
    /// run" without a mouse. See FF-23.</summary>
    private bool _autoDemo;

    /// <summary>Start with the simulation paused. Exists for the G6 self-test
    /// (FF-14): forcing an input tag while paused must still reach the bus,
    /// and that needs an engine that is paused before anything can connect.</summary>
    private bool _startPaused;

    /// <summary>Dump the `describe` table to stdout and exit, so a script can
    /// ask what a scene exposes without opening a WebSocket. See UX-14.</summary>
    private bool _printTags;

    /// <summary>Tag bus port, overriding TagBusServer's 7411 default. Every
    /// self-test binds the bus, so two engine runs on one machine collide on
    /// the default port: the loser retries for three seconds, gives up, and
    /// then simulates perfectly while listening on nothing. --bus-port=N lets
    /// concurrent runs coexist. -1 means "leave the default alone".</summary>
    private int _busPort = -1;

    public override void _Ready()
    {
        foreach (var arg in OS.GetCmdlineUserArgs())
        {
            if (arg.StartsWith("--duration="))
                _runFor = arg.Substring("--duration=".Length).ToFloat();
            else if (arg.StartsWith("--screenshot="))
                _screenshotPath = arg.Substring("--screenshot=".Length);
            else if (arg.StartsWith("--screenshot-at="))
                _screenshotAt = arg.Substring("--screenshot-at=".Length).ToFloat();
            else if (arg == "--deterministic")
                _deterministic = true;
            else if (arg == "--physics")
                _deterministic = false;   // kept: it used to be the opt-in flag
            else if (arg.StartsWith("--time-scale="))
                _timeScale = arg.Substring("--time-scale=".Length).ToFloat();
            else if (arg.StartsWith("--self-test="))
                _selfTest = arg.Substring("--self-test=".Length);
            else if (arg.StartsWith("--scene="))
                _scenePath = arg.Substring("--scene=".Length);
            else if (arg == "--demo")
                _autoDemo = true;
            else if (arg == "--paused")
                _startPaused = true;
            else if (arg == "--print-tags")
                _printTags = true;
            else if (arg.StartsWith("--bus-port="))
                _busPort = arg.Substring("--bus-port=".Length).ToInt();
        }

        // A fixed regression scene, not a template — the deterministic
        // scene is the regression contract's own fixed tag set, and --scene=
        // asks to load a template's tags into it instead. Reject the combination
        // rather than silently publishing whichever tags happened to end up in
        // the table.
        if (_deterministic && _scenePath is { Length: > 0 })
        {
            GD.PrintErr("--deterministic and --scene= cannot combine: " +
                        "--deterministic is the fixed regression scene, not a template. " +
                        "Drop one of the two flags.");
            GetTree().Quit(1);
            return;
        }

        // Rigid bodies by default — the parts are real colliders, so properties,
        // collisions and a held-out pusher all behave physically.
        //
        // --deterministic swaps in the fixed-timestep scene instead. That one is
        // the regression contract: it advances by exactly TickMs and mirrors
        // harness/scene.py, so the same sidecar drives both engines to the same
        // counts. Jolt cannot promise that, so anything asserting exact numbers
        // (tools/drive_engine.py, CI) must ask for it explicitly.
        _scene = _deterministic ? new SortingScene() : null;

        var tags = new TagTable();
        if (_scene is not null) tags = _scene.Tags;
        else SortingTags.Declare(tags);

        _bus = new TagBusServer { Name = "TagBus", Tags = tags, SceneName = SceneName };
        if (_busPort > 0) _bus.Port = _busPort;
        AddChild(_bus);

        _sim = new SimulationControls { Name = "SimulationControls" };
        AddChild(_sim);
        if (_timeScale > 0.0f) _sim.SetRate(_timeScale);
        if (_startPaused) _sim.SetPaused(true);
        _startedAt = Time.GetTicksMsec() / 1000.0;

        // The renderer is optional: headless CI runs the same scene with no view
        // at all, which is why SceneView only ever reads simulation state.
        if (DisplayServer.GetName() != "headless")
        {
            BuildView(tags);
        }
        else
        {
            // Headless physics needs a floor to land on; the deterministic
            // scene simulates its own boxes and has nothing to drop.
            if (!_deterministic) StudioEnvironment.AddFloor(this, withGrid: false);
            BuildHeadlessParts(tags);
        }

        // Report the bus state rather than announcing "ready" regardless: an
        // instance that could not bind the port simulates perfectly and is
        // unreachable, and saying "ready" sends people to debug their PLC.
        //
        // _bus.SceneName, not the SceneName const: a loaded template already
        // adopted its own name onto the bus by this point (BuildView and
        // BuildHeadlessPhysicsParts both call AdoptSceneName before returning),
        // so printing the const here reported "sorting-by-height" for every
        // template while the bus itself correctly told drivers otherwise (§2.10).
        GD.Print($"FactoryForge engine ready — {(_deterministic ? "DETERMINISTIC" : "PHYSICS")} " +
                 $"scene '{_bus.SceneName}', {tags.Count} tags" +
                 (_bus.IsListening ? $", bus on {_bus.Port}"
                                   : "  [NO TAG BUS — port in use, drivers cannot connect]"));

        if (_printTags)
        {
            var msg = new JsonObject
            {
                ["t"] = "describe",
                ["scene"] = _bus.SceneName,
                ["epoch"] = _bus.Epoch,
                ["tags"] = tags.ToJson(),
            };
            GD.Print(msg.ToJsonString());
            GetTree().Quit(0);
            return;
        }

        // Added last so it runs after the editor each tick, and therefore reads
        // the tags as the part dispatch left them rather than a tick behind.
        if (_selfTest == "buttons")
        {
            AddChild(new PanelSelfTest { Name = "PanelSelfTest", Tags = tags, Editor = _editor });
        }
        if (_selfTest == "dragpath")
        {
            AddChild(new DragPathSelfTest { Name = "DragPathSelfTest", Editor = _editor! });
        }
        if (_selfTest == "fault")
        {
            AddChild(new FaultInjectionSelfTest { Name = "FaultInjectionSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "drag")
        {
            AddChild(new DragMoveSelfTest { Name = "DragMoveSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "setpoint")
        {
            AddChild(new SetpointDialSelfTest { Name = "SetpointDialSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "templates")
        {
            AddChild(new TemplateSelfTest { Name = "TemplateSelfTest", Tags = tags, Editor = _editor });
        }
        if (_selfTest == "scene")
        {
            AddChild(new SceneSelfTest { Name = "SceneSelfTest", Tags = tags, Editor = _editor });
        }
        if (_selfTest == "io")
        {
            AddChild(new IoSelfTest { Name = "IoSelfTest", Tags = tags, Editor = _editor });
        }
        if (_selfTest == "click")
        {
            AddChild(new ClickPathSelfTest { Name = "ClickPathSelfTest", Tags = tags, Editor = _editor });
        }
        if (_selfTest == "parity")
        {
            AddChild(new TagParitySelfTest { Name = "TagParitySelfTest" });
        }
        if (_selfTest == "layout")
        {
            AddChild(new LayoutSelfTest { Name = "LayoutSelfTest" });
        }
        if (_selfTest == "force")
        {
            AddChild(new TagForceSelfTest { Name = "TagForceSelfTest" });
        }
        if (_selfTest == "demo")
        {
            AddChild(new DemoDriverSelfTest { Name = "DemoDriverSelfTest", Tags = tags });
        }
        if (_selfTest == "startstop")
        {
            AddChild(new StartStopProfileSelfTest { Name = "StartStopProfileSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "tank")
        {
            AddChild(new TankProfileSelfTest { Name = "TankProfileSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "lightcurtain")
        {
            AddChild(new LightCurtainProfileSelfTest { Name = "LightCurtainProfileSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "roller")
        {
            AddChild(new RollerProfileSelfTest { Name = "RollerProfileSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "refusal")
        {
            AddChild(new DemoRefusalSelfTest { Name = "DemoRefusalSelfTest" });
        }
        if (_selfTest == "proppanel")
        {
            AddChild(new PartPropertyPanelSelfTest { Name = "PartPropertyPanelSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "operate")
        {
            AddChild(new ClickOperateSelfTest { Name = "ClickOperateSelfTest", Tags = tags, Editor = _editor! });
        }
        if (_selfTest == "modehint")
        {
            AddChild(new RunModeHintSelfTest { Name = "RunModeHintSelfTest", Editor = _editor! });
        }
        if (_selfTest == "tryscene")
        {
            AddChild(new TryThisSceneSelfTest { Name = "TryThisSceneSelfTest", Editor = _editor! });
        }
        if (_selfTest == "scenes")
        {
            AddChild(new SceneTagSetSelfTest { Name = "SceneTagSetSelfTest", Editor = _editor!, Tags = tags });
        }
        if (_selfTest == "modes")
        {
            AddChild(new ModeSelfTest { Name = "ModeSelfTest", Editor = _editor!, Tags = tags });
        }
        if (_selfTest == "sidecar")
        {
            AddChild(new SidecarLocatorSelfTest { Name = "SidecarLocatorSelfTest" });
        }
        if (_selfTest == "partsettings")
        {
            AddChild(new PartSettingsSelfTest { Name = "PartSettingsSelfTest", Editor = _editor!, Tags = tags });
        }
        if (_selfTest == "newparts")
        {
            AddChild(new NewPartsSelfTest { Name = "NewPartsSelfTest", Editor = _editor!, Tags = tags });
        }
        if (_selfTest == "lineparts")
        {
            AddChild(new LinePartsSelfTest { Name = "LinePartsSelfTest", Editor = _editor!, Tags = tags });
        }
        if (_selfTest == "buildflow")
        {
            AddChild(new BuildFlowSelfTest { Name = "BuildFlowSelfTest", Editor = _editor!, Tags = tags });
        }
    }

    private void BuildView(TagTable tags)
    {
        // Intercept the window's close button so an unsaved scene gets the
        // same prompt as Home and Load, instead of vanishing silently. Only
        // windowed interactive runs reach BuildView; headless CI and the
        // harness's own GetTree().Quit() calls are untouched by this.
        GetTree().AutoAcceptQuit = false;

        // Below this, the start screen's 980x640 card and the parts palette
        // both start clipping (FF-17, FF-18). project.godot sets a sane
        // default size; this is the floor a user can still resize down to.
        GetWindow().MinSize = new Vector2I(1000, 700);

        StudioEnvironment.AddEnvironment(this);
        StudioEnvironment.AddFloor(this);

        // The ghost-box view exists only for the deterministic scene; the
        // physics scene's cartons are real nodes that draw themselves.
        if (_scene is not null)
        {
            var view = new SceneView { Name = "View" };
            AddChild(view);
            view.Build(_scene);
        }

        AddChild(new OrbitCamera { Name = "OrbitCamera", Current = true });
        AddChild(new FreeLookCamera { Name = "FreeLookCamera", Current = false });

        var inspectorUI = new TagInspectorUI { Name = "TagInspectorUI" };
        AddChild(inspectorUI);
        inspectorUI.Setup(tags);

        var propertyInspector = new PartPropertyInspectorUI { Name = "PartPropertyInspectorUI" };
        AddChild(propertyInspector);

        var editor = new SceneEditor
        {
            Name = "SceneEditor",
            Grid = GetNode<VoxelGrid>("VoxelGrid"),
            Tags = tags,
            Scene = _scene,
            TagInspector = inspectorUI,
            PropertyInspector = propertyInspector
        };
        AddChild(editor);
        propertyInspector.Editor = editor;
        // Before the first scene is registered, so the very first line the user
        // sees is framed too (CP-16).
        editor.SceneLoaded += FrameWholeScene;
        editor.RegisterDefaultSceneParts(physical: !_deterministic);
        _editor = editor;
        _propertyInspector = propertyInspector;

        var paletteUI = new PartPaletteUI { Name = "PartPaletteUI" };
        paletteUI.PartSelected += (partType) => editor.SetPlacementPart(partType);
        // The palette follows the editor, not its own button: the placement
        // tool stays armed after a placement (BF-01) and is put down by four
        // things the palette never hears about.
        editor.PlacementArmedChanged += (partType) => paletteUI.SetArmed(partType);

        // The box-select rectangle. Added before the rest of the UI so it sits
        // under the panels: a rectangle drawn over the palette would obscure
        // the very buttons somebody is dragging away from.
        var selectionRect = new SelectionRectUI { Name = "SelectionRectUI" };
        AddChild(selectionRect);
        editor.SelectionRectChanged += (rect, active) => selectionRect.SetRect(rect, active);
        AddChild(paletteUI);

        var wiringUI = new DriverWiringUI { Name = "DriverWiringUI" };
        AddChild(wiringUI);
        wiringUI.Setup(tags);

        var driverConnectionUI = new DriverConnectionUI { Name = "DriverConnectionUI" };
        AddChild(driverConnectionUI);

        // An in-engine stand-in PLC, so the app demonstrates itself with
        // nothing installed (FF-23). Checks _bus.HasClient every frame and
        // stands itself down the instant a real driver connects.
        var demo = new DemoDriver { Name = "DemoDriver", Tags = tags, Bus = _bus };
        AddChild(demo);

        var idleHint = new IdleHintUI { Name = "IdleHintUI", Bus = _bus, Tags = tags, Demo = demo };
        AddChild(idleHint);

        var toolbarUI = new SceneToolbarUI { Name = "SceneToolbarUI" };
        toolbarUI.SaveRequested += (path) =>
        {
            editor.SaveSceneToFile(path);
            StartScreenUI.Remember(path);
        };
        toolbarUI.LoadRequested += (path) =>
        {
            void DoLoad()
            {
                demo.Stop();
                editor.LoadSceneFromFile(path);
                StartScreenUI.Remember(path);
                AdoptSceneName(editor);
            }

            if (editor.IsDirty)
            {
                EditorConfirm.Ask(this, "Discard unsaved changes?",
                    $"Loading '{path.GetFile()}' discards the current unsaved scene. Continue?",
                    DoLoad);
            }
            else DoLoad();
        };
        toolbarUI.ClearRequested += () =>
        {
            if (!editor.HasPlacedParts) { editor.ClearAllPlacedPartsWithUndo(); return; }
            EditorConfirm.Ask(this, "Clear the scene?",
                "This removes every part from the scene. Ctrl+Z immediately after will bring it back.",
                editor.ClearAllPlacedPartsWithUndo);
        };
        toolbarUI.WiringRequested += () => wiringUI.ToggleVisibility();
        toolbarUI.DriverRequested += () => driverConnectionUI.ToggleVisibility();
        toolbarUI.PauseToggled += () => _sim.TogglePause();
        toolbarUI.ResetRequested += ResetSimulation;
        toolbarUI.RateSelected += (rate) => _sim.SetRate(rate);
        toolbarUI.ModeToggled += () => editor.ToggleMode();
        toolbarUI.DemoToggled += () =>
        {
            if (demo.Active) demo.Stop();
            else demo.Start();
        };
        toolbarUI.Bus = _bus;
        toolbarUI.Demo = demo;
        toolbarUI.Editor = editor;
        toolbarUI.IdleHint = idleHint;
        driverConnectionUI.Editor = editor;
        driverConnectionUI.IdleHint = idleHint;
        editor.Toolbar = toolbarUI;
        editor.IdleHint = idleHint;
        toolbarUI.FaultToolToggled += () => editor.SetFaultToolArmed(!editor.FaultToolArmed);
        toolbarUI.PartNamesToggled += () => editor.TogglePartNames();
        toolbarUI.TaskBriefRequested += ToggleTaskBrief;
        toolbarUI.HelpRequested += () => GetNodeOrNull<KeyHelpUI>("KeyHelpUI")?.Toggle();
        AddChild(toolbarUI);

        // After every other panel, because CanvasItem siblings draw in tree
        // order and an overlay behind the parts palette is an overlay nobody
        // can read. Still before the start screen, which owns the very top.
        AddChild(new KeyHelpUI { Name = "KeyHelpUI" });
        AddChild(new TaskBriefUI { Name = "TaskBriefUI" });

        // The start screen goes on last so it draws over everything, and it is
        // only ever a GUI thing — headless runs and the self-tests never see it.
        var startScreen = new StartScreenUI { Name = "StartScreenUI" };
        AddChild(startScreen);
        // Every scene-changing path adopts the name onto the bus (AdoptSceneName)
        // -- these two used not to, so choosing "Sorting by height" after a
        // template left the bus reporting the template's name for a scene that
        // no longer matched it, and an empty scene reported whatever name was
        // already stale rather than "untitled". A driver -- or DemoDriver's own
        // profile lookup -- trusts this name; a custom scene must report one
        // no profile matches, or Demo silently no-ops instead of refusing (UX-30).
        startScreen.DefaultSceneChosen += () => { demo.Stop(); editor.LoadDefaultSortingScene(); AdoptSceneName(editor); };
        startScreen.EmptySceneChosen += () => { demo.Stop(); editor.NewEmptyScene(); AdoptSceneName(editor); };
        startScreen.TemplateChosen += (path) =>
        {
            demo.Stop();
            editor.LoadTemplate(path);
            AdoptSceneName(editor);
        };
        startScreen.OpenRequested += (path) =>
        {
            demo.Stop();
            editor.LoadTemplate(path);
            StartScreenUI.Remember(path);
            AdoptSceneName(editor);
        };
        startScreen.DemoRequested += () =>
        {
            editor.LoadDefaultSortingScene();
            demo.Start();
        };
        if (_selfTest is not null) startScreen.Visible = false;
        toolbarUI.StartScreenRequested += () =>
        {
            if (editor.IsDirty)
            {
                EditorConfirm.Ask(this, "Leave this scene?",
                    "You have unsaved changes. Discard them and return to the start screen?",
                    startScreen.Reopen);
            }
            else startScreen.Reopen();
        };

        // An explicitly requested scene means the choice has already been made.
        // --demo composes with it (UX-11): load X, then start the demo against
        // whatever X turned out to be, rather than --demo only ever meaning "load
        // the default sorting scene".
        if (_scenePath is { Length: > 0 })
        {
            startScreen.Visible = false;
            editor.LoadTemplate(_scenePath);
            AdoptSceneName(editor);
            if (_autoDemo) demo.Start();
        }
        else if (_autoDemo)
        {
            startScreen.Visible = false;
            editor.LoadDefaultSortingScene();
            demo.Start();
        }

        // Mode lives on the editor; the toolbar and palette only reflect it, so
        // the F1 key and the button can never disagree about which mode we are in.
        // Placing, deleting or renaming a part changes the I/O list. Republish
        // it, or a driver connected while you build keeps working from the tag
        // list it was handed at connect time and never sees the new parts.
        editor.TagsChanged += () =>
        {
            wiringUI.RebuildWiringList();
            _bus.SendDescribe();
        };

        editor.ModeChanged += (running) =>
        {
            toolbarUI.ShowMode(running);
            paletteUI.ShowForMode(running);
            if (running)
            {
                var (count, kinds) = editor.DescribeOperableParts();
                idleHint.ShowModeEnteredHint(count, kinds, editor.HasTurnablePot());
            }
        };
        toolbarUI.ShowMode(editor.Mode == EditorMode.Run);
        paletteUI.ShowForMode(editor.Mode == EditorMode.Run);

        // The toolbar mirrors the state rather than owning it, so the keyboard
        // shortcuts and the buttons can never disagree.
        _sim.StateChanged += (paused, rate) => toolbarUI.ShowState(paused, rate);
        toolbarUI.ShowState(_sim.Paused, _sim.Rate);
    }

    /// <summary>
    /// The window's close button (or Alt+F4) raises this instead of quitting
    /// outright, since <see cref="BuildView"/> turned off auto-accept. An
    /// unsaved scene gets the same confirmation Home and Load already give it;
    /// anything else — no editor (headless), or nothing unsaved — quits at once.
    /// </summary>
    public override void _Notification(int what)
    {
        if (what != (int)NotificationWMCloseRequest) return;

        if (_editor is { IsDirty: true })
        {
            EditorConfirm.Ask(this, "Quit FactoryForge?",
                "You have unsaved changes. Quit anyway?",
                () => GetTree().Quit());
        }
        else
        {
            GetTree().Quit();
        }
    }

    /// <summary>
    /// Report the loaded scene under its own name. A driver holds the tag list
    /// and scene name it was given at connect time, so switching scenes without
    /// this leaves it convinced it is still talking to the sorting demo.
    /// </summary>
    private void AdoptSceneName(SceneEditor editor)
    {
        _bus.SceneName = editor.SceneName;
        _bus.SendDescribe();
        // The property panel's empty state already showed itself once, mid-load,
        // for whatever scene was loaded *before* this one -- ClearAllPlacedParts
        // deselects (and so redraws the empty state) before SceneName is updated
        // to the new value. Redrawing again here, after the swap is complete, is
        // what makes the blurb match what actually loaded (UX-32) rather than a
        // scene that no longer exists.
        _propertyInspector?.RefreshIfEmpty();
    }

    /// <summary>Physics mode without a renderer: no UI, but the parts still have
    /// to exist or nothing moves. A requested template loads the same way the
    /// windowed path does (UX-10) -- SceneEditor builds parts from JSON with no
    /// renderer, camera or grid involved, so nothing here is display-dependent.</summary>
    /// <summary>
    /// The headless counterpart of <see cref="BuildView"/>: the same parts,
    /// minus everything that needs a screen.
    ///
    /// This runs for the deterministic scene too, and that is the point.
    /// Headless <c>--deterministic</c> used to build no parts at all, so it
    /// published 11 tags where the very same flag published 17 with a window
    /// open -- no operator panel, and therefore no Start, no Stop and no
    /// E-stop. A program written against the scene on screen would then fail
    /// against the scene in CI, which is exactly backwards for the mode whose
    /// whole purpose is repeatable grading (docs/GETTING_STARTED.md sends
    /// people here for it).
    ///
    /// <paramref name="tags"/> already belongs to <see cref="SortingScene"/>
    /// in that mode, so the parts register as <em>views</em> of tags the scene
    /// owns and simulates (<c>physical: false</c>) rather than as authorities
    /// that would fight it for them. The panel is the exception either way: it
    /// owns its own tags, because nothing in the deterministic scene presses
    /// buttons.
    /// </summary>
    private void BuildHeadlessParts(TagTable tags)
    {
        var editor = new SceneEditor { Name = "SceneEditor", Tags = tags };
        AddChild(editor);
        if (_scenePath is { Length: > 0 })
        {
            editor.LoadTemplate(_scenePath);
            AdoptSceneName(editor);
        }
        else
        {
            editor.RegisterDefaultSceneParts(physical: !_deterministic);
        }
        _editor = editor;
    }

    public override void _UnhandledInput(InputEvent @event)
    {
        if (DisplayServer.GetName() == "headless") return;

        if (@event is InputEventKey keyEvent && keyEvent.Pressed && !keyEvent.Echo)
        {
            if (keyEvent.Keycode == Key.F4)
            {
                var wiringUI = GetNodeOrNull<DriverWiringUI>("DriverWiringUI");
                wiringUI?.ToggleVisibility();
            }
            else if (keyEvent.Keycode == Key.F5)
            {
                var driverConnectionUI = GetNodeOrNull<DriverConnectionUI>("DriverConnectionUI");
                driverConnectionUI?.ToggleVisibility();
            }
            else if (keyEvent.Keycode == Key.Space)
            {
                _sim.TogglePause();
                GD.Print(_sim.Paused ? "Simulation paused" : "Simulation running");
            }
            else if (keyEvent.CtrlPressed && keyEvent.Keycode == Key.R)
            {
                ResetSimulation();
            }
            else if (keyEvent.Keycode == Key.F)
            {
                FrameSelection();
            }
            else if (keyEvent.Keycode == Key.F12)
            {
                GetNodeOrNull<KeyHelpUI>("KeyHelpUI")?.Toggle();
            }
            else if (keyEvent.Keycode == Key.T && !keyEvent.CtrlPressed)
            {
                ToggleTaskBrief();
            }
            // 1-4: standard viewing angles (NV-03). They change the angle and
            // not the subject, so whatever you were looking at stays framed.
            else if (ViewPresetFor(keyEvent.Keycode) is { } preset)
            {
                GetNodeOrNull<OrbitCamera>("OrbitCamera")?.ViewFrom(preset);
            }
            else if (keyEvent.Keycode == Key.Escape)
            {
                // Only if an overlay is actually open: Escape also cancels a
                // placement and disarms the fault tool, and spending it here
                // unconditionally would break both.
                GetNodeOrNull<KeyHelpUI>("KeyHelpUI")?.DismissIfOpen();
                GetNodeOrNull<TaskBriefUI>("TaskBriefUI")?.DismissIfOpen();
            }
            else if (keyEvent.Keycode == Key.C)
            {
                var orbitCam = GetNode<OrbitCamera>("OrbitCamera");
                var flyCam = GetNode<FreeLookCamera>("FreeLookCamera");

                if (orbitCam.Current)
                {
                    flyCam.GlobalTransform = orbitCam.GlobalTransform;
                    flyCam.SyncRotationFromTransform();
                    flyCam.MakeCurrent();
                    GD.Print("Camera mode: Free-Look Fly Camera (WASD + Right-Click drag)");
                }
                else
                {
                    orbitCam.MakeCurrent();
                    Input.MouseMode = Input.MouseModeEnum.Visible;
                    GD.Print("Camera mode: Orbit Camera (Left-Click drag + Scroll wheel)");
                }
            }
        }
    }

    /// <summary>
    /// Put the camera on the selection, or on the whole line if nothing is
    /// selected (CP-16).
    ///
    /// Only the orbit camera is framed: the free-look camera is a flying one
    /// and moving it under the user is disorienting rather than helpful, so F
    /// aims the orbit camera and switching back with C arrives somewhere
    /// sensible.
    /// </summary>
    private void FrameSelection()
    {
        if (_editor?.FocusBounds() is not { } bounds) return;
        var orbit = GetNodeOrNull<OrbitCamera>("OrbitCamera");
        if (orbit is null) return;

        orbit.Frame(bounds);
        if (!orbit.Current)
        {
            orbit.MakeCurrent();
            Input.MouseMode = Input.MouseModeEnum.Visible;
        }
    }

    /// <summary>
    /// Frame a scene that has just been opened. Deselects nothing and moves no
    /// state — it only aims the camera, which is what opening a template ought
    /// to do and did not: the default pose put a control panel across the whole
    /// frame with the line behind it, so every template opened looking like the
    /// same close-up of a panel.
    ///
    /// Headless runs have no camera node; the guard in FrameSelection covers
    /// that, and this is connected before the first scene is registered so
    /// there is exactly one path.
    /// </summary>
    /// <summary>The number keys' viewing angles, as one table so the key list
    /// and the handler cannot drift.</summary>
    private static OrbitCamera.CameraPreset? ViewPresetFor(Key keycode) => keycode switch
    {
        Key.Key1 => OrbitCamera.CameraPreset.Iso,
        Key.Key2 => OrbitCamera.CameraPreset.Top,
        Key.Key3 => OrbitCamera.CameraPreset.Front,
        Key.Key4 => OrbitCamera.CameraPreset.Side,
        _ => null,
    };

    /// <summary>Open or close the brief for whatever scene is loaded (BR-03).
    /// The editor knows the scene's name and the manifest knows its lesson, so
    /// neither has to hold a copy of the other.</summary>
    private void ToggleTaskBrief()
    {
        if (_editor is null) return;
        GetNodeOrNull<TaskBriefUI>("TaskBriefUI")?.Toggle(_editor.SceneName);
    }

    private void FrameWholeScene()
    {
        if (DisplayServer.GetName() == "headless") return;
        if (_editor?.FocusBounds() is not { } bounds) return;
        GetNodeOrNull<OrbitCamera>("OrbitCamera")?.Frame(bounds, overviewPitch: true);
    }

    /// <summary>Restart the run without disturbing the scene you built: boxes
    /// cleared, counters zeroed, machines left where they are.</summary>
    private void ResetSimulation()
    {
        _scene?.Reset();
        _editor?.ResetItems();
        _bus.SendUpdates();
        GD.Print("Simulation reset");
    }

    public override void _Process(double delta)
    {
        // GetTree().Quit() schedules the exit for end of frame rather than
        // stopping it immediately -- an invalid-argument rejection in _Ready
        // (UX-12) returns before _bus exists, and without this guard the tree
        // still ran one more _Process and crashed on a null reference instead
        // of exiting cleanly on the message already printed.
        if (_bus is null) return;

        // Fixed timestep with a wall-clock accumulator. The scene always
        // advances by exactly TickMs, never by measured elapsed time: a
        // variable dt would make runs non-reproducible and defeat the
        // regression scene. See docs/PLAN.md.
        // The accumulator runs on *simulation* time, so the time-scale control
        // speeds the line up and down. --duration and --screenshot-at run on
        // wall-clock: they are harness controls, and a paused run whose clock
        // also stopped would never reach its duration and would hang forever.
        double interval = _bus.TickMs / 1000.0;
        _accumulator += delta;
        _elapsed = Time.GetTicksMsec() / 1000.0 - _startedAt;

        int steps = 0;
        while (_accumulator >= interval && steps < MaxCatchUpSteps)
        {
            // The physics scene advances itself on Jolt's clock; this loop then
            // only paces the bus.
            _scene?.Tick(interval);
            _bus.CountTick();
            _accumulator -= interval;
            steps++;
        }
        if (_accumulator > interval * MaxCatchUpSteps) _accumulator = 0;
        // Unconditional: a paused sim (TimeScale=0, steps stays 0) still needs
        // forced tag changes from the inspector to reach the bus. Delta-only
        // encoding means an idle scene still produces zero traffic.
        _bus.SendUpdates();

        if (_screenshotPath is not null && _screenshotAt >= 0 && _elapsed >= _screenshotAt)
        {
            _screenshotAt = -1;
            CallDeferred(nameof(SaveScreenshot));
        }

        if (_runFor > 0 && _elapsed >= _runFor)
        {
            // Quit first. This block used to read the sorting demo's counters
            // unconditionally, and a scene that does not have them threw here —
            // *before* Quit, so the run never ended and the same exception was
            // raised every frame forever. An idle tank template produced a 23 MB
            // log of identical stack traces and looked like a hang.
            string counts = _scene is { } scene
                ? $"tall={scene.SortedTall.Count} short={scene.SortedShort.Count} belt={scene.Boxes.Count}"
                : Counters();
            GD.Print($"done: tick={_bus.TickCount} {counts}");
            GetTree().Quit();
        }
    }

    /// <summary>
    /// The sorting demo's counters, when this scene has them. A loaded scene may
    /// not — the tags are declared for the built-in line and dropped when you
    /// switch away from it, so this has to ask rather than assume.
    /// </summary>
    private string Counters()
    {
        if (!_bus.Tags.Contains(SortingTags.CounterTall)) return "no counters in this scene";
        return $"tall={_bus.Tags.Visible(SortingTags.CounterTall)} " +
               $"short={_bus.Tags.Visible(SortingTags.CounterShort)}";
    }

    private void SaveScreenshot()
    {
        var image = GetViewport().GetTexture().GetImage();
        var err = image.SavePng(_screenshotPath);
        GD.Print(err == Error.Ok
            ? $"screenshot saved to {_screenshotPath}"
            : $"screenshot failed: {err}");
    }
}
