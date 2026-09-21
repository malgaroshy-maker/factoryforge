(* ------------------------------------------------------------------------
   FactoryForge - sorting by height, in IEC 61131-3 Structured Text.

   This is the OpenPLC half of examples/tia/Sorting.scl. Same scene, same
   sequence, same numbers; a different controller and a different wire.
   Nothing here is FactoryForge-specific: it is a plain ST program with
   located variables, and OpenPLC's Modbus master is what connects those
   addresses to the simulator.

   The simulator is the Modbus *slave*. OpenPLC is the master and polls it.
   Start the sidecar's Modbus driver, add it in OpenPLC as a Slave Device
   ("Generic Modbus TCP Device"), and OpenPLC lays the slave's points out in
   its own master address block, which always starts at 100:

     slave discrete inputs  -> %IX100.0, %IX100.1, ...
     slave coils            -> %QX100.0, %QX100.1, ...
     slave input registers  -> %IW100,   %IW101,   ...
     slave holding regs (W) -> %QW100,   %QW101,   ...

   The order inside each block is the order the sidecar prints, which for the
   sorting scene is sorted tag id. Run

     python -m factoryforge_sidecar connect --driver modbus-tcp -o port 5502

   and copy the map it prints. For this scene it is:

     0x0  conveyor.rotate       1x0  pusher.extended     3x0  counter.short
     0x1  emitter.emit          1x1  pusher.retracted    3x2  counter.tall
     0x2  pusher.extend         1x2  sensor_high.detect
     0x3  stack_light.green     1x3  sensor_low.detect

   THE COUNTERS ARE TWO REGISTERS EACH. A FactoryForge Int tag is a signed
   32-bit number, and since HP-49 the Modbus driver carries all 32 bits:
   two consecutive registers, big-endian, high word first. counter.short is
   3x0 AND 3x1; counter.tall is 3x2 AND 3x3. A master that reads one register
   reads half a number. OpenPLC's Modbus master has no notion of a 32-bit
   point, so the reassembly is done here, in the program, which is what you
   would have to do on any PLC that polls a generic Modbus device.

   Sequence for a tall box, unchanged from the SCL:
     1. leading edge breaks sensor_high at 1.9 m
     2. wait 900 ms while it travels to the pusher at 2.5 m
     3. extend for 1.5 s, which deflects it into the chute
     4. retract, ready for the next one

   Timing, from the geometry in harness/scene.py:
     belt 0.5 m/s, pusher catches within +/-0.15 m, stroke 0.3 s
     travel 1.9 -> 2.5 = 0.6 m / 0.5 m/s = 1.2 s
     lead   1.2 s - 0.3 s stroke        = 0.9 s   <- PUSH_DELAY

   The catch window is 0.9 s to 1.5 s after the edge. The pusher is fully out
   at PUSH_DELAY + 0.3 s = 1.2 s, dead centre, and everything between the
   sensor and the coil has to fit in the remaining 0.3 s: the master's
   polling period is paid twice -- once for the sensor state to reach the
   PLC, once for the coil to reach the simulator -- plus a task interval
   between them. At the recommended 50 ms polling period and a 20 ms task
   that is about 130 ms worst case. At OpenPLC's default 100 ms it is about
   230 ms of the 300 available, which fits and leaves nothing. Set the
   polling period to 50 ms.
   ------------------------------------------------------------------------ *)

PROGRAM sorting
  VAR
    (* --- PLC outputs: coils the program writes into the scene --------- *)
    ConveyorRotate  AT %QX100.0 : BOOL;   (* 0x0  conveyor.rotate        *)
    EmitterEmit     AT %QX100.1 : BOOL;   (* 0x1  emitter.emit           *)
    PusherExtend    AT %QX100.2 : BOOL;   (* 0x2  pusher.extend          *)
    StackLightGreen AT %QX100.3 : BOOL;   (* 0x3  stack_light.green      *)

    (* --- PLC inputs: discrete inputs the scene writes ----------------- *)
    PusherExtended  AT %IX100.0 : BOOL;   (* 1x0  pusher.extended        *)
    PusherRetracted AT %IX100.1 : BOOL;   (* 1x1  pusher.retracted       *)
    SensorHigh      AT %IX100.2 : BOOL;   (* 1x2  sensor_high.detect     *)
    SensorLow       AT %IX100.3 : BOOL;   (* 1x3  sensor_low.detect      *)

    (* --- PLC inputs: the counters, 32 bits each, high word first ------ *)
    ShortHi AT %IW100 : WORD;             (* 3x0  counter.short, high    *)
    ShortLo AT %IW101 : WORD;             (* 3x1  counter.short, low     *)
    TallHi  AT %IW102 : WORD;             (* 3x2  counter.tall,  high    *)
    TallLo  AT %IW103 : WORD;             (* 3x3  counter.tall,  low     *)

    (* --- the reassembled counters -------------------------------------
       At %MD0 and %MD1 so they are visible from outside the program:
       OpenPLC's own Modbus slave server publishes %MD<n> as holding
       registers 2048 + 2n and 2049 + 2n, so a master can read back what
       this program decided the counters were. That is the cross-check
       that the two-register format arrived intact.                      *)
    CountShort AT %MD0 : DINT;
    CountTall  AT %MD1 : DINT;
  END_VAR

  (* A second VAR block, and not for tidiness: matiec will not mix located
     and unlocated declarations in one. Put `tEmit : TON;` under the last
     `AT %...` line and every declaration after it is reported as an
     "invalid located variable declaration" -- and then every statement
     that uses one of them is reported as a syntax error, which is where
     you will start looking. *)
  VAR
    tEmit    : TON;      (* free-running half-period                     *)
    EmitFlag : BOOL;     (* toggles each half-period                     *)
    tDelay   : TON;      (* detection -> arrival at the pusher           *)
    tPush    : TON;      (* how long the pusher stays out                *)
    HighMem  : BOOL;     (* edge memory for SensorHigh                   *)
    PushReq  : BOOL;     (* a tall box is on its way to the pusher       *)
  END_VAR

  VAR CONSTANT
    (* One box per *rising* edge, so the box interval is twice this. *)
    EMIT_HALF_PERIOD : TIME := T#1s500ms;   (* -> a box every 3 s *)
    PUSH_DELAY       : TIME := T#900ms;
    PUSH_HOLD        : TIME := T#1s500ms;
  END_VAR

  (* --- run continuously ------------------------------------------- *)
  ConveyorRotate  := TRUE;
  StackLightGreen := TRUE;

  (* --- emit one box per rising edge --------------------------------
     EmitterEmit is a square wave: high for a full half-period, low for
     the next. The simulator emits one box on each rising edge.

     Why not a short pulse? A polled transport samples; it does not
     capture every transition. OpenPLC's Modbus master writes the coil
     once per polling period, so a level held for one task scan is a
     level the simulator may never see. 1.5 s cannot be missed.

     IN is held TRUE so tEmit.Q latches when ET reaches PT, and stays
     latched until reset explicitly below. Do not write this as
         tEmit(IN := NOT tEmit.Q, PT := EMIT_HALF_PERIOD);
     which makes Q high for a single scan and relies on the toggle
     running in exactly that scan. That version failed on a real
     S7-1500 (AGENTS.md gotcha 5); it is kept out of here for the same
     reason, not because OpenPLC was caught doing it.                  *)
  tEmit(IN := TRUE, PT := EMIT_HALF_PERIOD);
  IF tEmit.Q THEN
    EmitFlag := NOT EmitFlag;
    tEmit(IN := FALSE, PT := EMIT_HALF_PERIOD);   (* restarts next scan *)
  END_IF;
  EmitterEmit := EmitFlag;

  (* --- latch a push request on the rising edge of SensorHigh --------
     The box clears the sensor long before it reaches the pusher, so the
     request has to outlive the signal that created it.                *)
  IF SensorHigh AND NOT HighMem THEN
    PushReq := TRUE;
  END_IF;
  HighMem := SensorHigh;

  (* --- delay, then stroke the pusher ------------------------------- *)
  tDelay(IN := PushReq,  PT := PUSH_DELAY);
  tPush (IN := tDelay.Q, PT := PUSH_HOLD);

  PusherExtend := tDelay.Q AND NOT tPush.Q;

  (* Clearing the request drops tDelay.Q, which resets tPush too. *)
  IF tPush.Q THEN
    PushReq := FALSE;
  END_IF;

  (* --- reassemble the 32-bit counters ------------------------------
     High word first, as struct.pack(">i") lays it out in the driver.
     DWORD_TO_DINT reinterprets the bit pattern rather than clamping,
     so this is correct for negative values too - not that a carton
     counter reaches one, but the tag type is signed and half a
     conversion is the kind of thing that only shows up later.        *)
  CountShort := DWORD_TO_DINT(SHL(IN := WORD_TO_DWORD(ShortHi), N := 16)
                              OR WORD_TO_DWORD(ShortLo));
  CountTall  := DWORD_TO_DINT(SHL(IN := WORD_TO_DWORD(TallHi), N := 16)
                              OR WORD_TO_DWORD(TallLo));

END_PROGRAM


CONFIGURATION Config0

  RESOURCE Res0 ON PLC
    (* 20 ms. The sequence's tightest margin is the 0.3 s of slack in the
       pusher's catch window, and every scan spent waiting eats into it. *)
    TASK Main(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM Inst0 WITH Main : sorting;
  END_RESOURCE

END_CONFIGURATION
