# DG_controller.py
"""
Diesel Generator supervisory controller (setpoint nudger) for RTDS + GTNET-SKT Multi

Design intent (realistic + thesis-friendly):
- DOES NOT retune physical parameters (Kp/Ki, droop, time constants, AVR gains, protection).
- ONLY adjusts online setpoints and operational bits:
    * REM_Wref      (float)  : speed reference / frequency bias
    * REM_PREF      (float)  : power/load reference bias (dispatch-like)
    * REM_BLOCKGEN  (int)    : block/enable (your logic uses BLOCKGEN)
    * REM_RESETGEN  (int)    : reset latch (pushbutton-like pulse)

How to use in your architecture:
- Another module reads GTNET outputs (PGEN, SMACH, GENRMSPU, OVERLOADED, BRKGEN, etc.).
- Another module injects "data degradation" by corrupting/delaying/quantizing those measurements.
- You feed the (possibly degraded) measurements into this controller.
- This controller outputs the 4 commands above to your SendIntAndFloatData / comms interface.

This file is *pure control logic* (no sockets), so it’s testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
import time
import math


# -----------------------------
# Small utilities
# -----------------------------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def sign(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)

@dataclass
class LowPass:
    """First-order low-pass filter."""
    tau: float
    y: Optional[float] = None

    def step(self, x: float, dt: float) -> float:
        if self.y is None:
            self.y = x
            return self.y
        if self.tau <= 0:
            self.y = x
            return self.y
        a = dt / (self.tau + dt)
        self.y = (1 - a) * self.y + a * x
        return self.y

@dataclass
class RateLimiter:
    """Limits rate of change of a signal."""
    up: float     # units per second
    down: float   # units per second
    y: Optional[float] = None

    def step(self, x: float, dt: float) -> float:
        if self.y is None:
            self.y = x
            return self.y
        dy = x - self.y
        max_up = self.up * dt
        max_dn = self.down * dt
        if dy > max_up:
            dy = max_up
        elif dy < -max_dn:
            dy = -max_dn
        self.y += dy
        return self.y


# -----------------------------
# RTDS measurement + command maps
# -----------------------------

@dataclass
class DGMeasurements:
    """Measurements coming from RTDS (GTNET -> Python) for the diesel generator channel."""
    # Handshake / diagnostics (optional but useful)
    NewDataSeq: Optional[int] = None
    ReadyToSend: Optional[int] = None
    SocketOverflow: Optional[int] = None
    InvalidMsg: Optional[int] = None

    # Electrical / mechanical
    PGEN: Optional[float] = None      # electrical P (pu or MW depending on your model)
    QGEN: Optional[float] = None
    PMACH: Optional[float] = None     # mechanical P
    QMACH: Optional[float] = None
    SMACH: Optional[float] = None     # apparent power magnitude (your overload logic uses this)
    GENRMSPU: Optional[float] = None  # terminal voltage pu (meter shows rms pu)

    # Status / protection
    OVERLOADED: Optional[int] = None  # 0/1
    BRKGEN: Optional[int] = None      # 0/1 (if available)

    # Optional frequency/speed (if you have it, feed it; if not, controller can still run)
    WPU: Optional[float] = None       # pu speed/frequency, nominal ~1.0


@dataclass
class DGCommands:
    """Commands to RTDS (Python -> GTNET) for the diesel generator channel."""
    REM_BLOCKGEN: int = 1
    REM_Wref: float = 1.0
    REM_PREF: float = 0.0
    REM_RESETGEN: int = 0


# -----------------------------
# Controller configuration
# -----------------------------

@dataclass
class DGControllerConfig:
    # --- Base setpoints ---
    Wref_base: float = 1.0     # nominal speed/frequency reference (pu)
    Pref_base: float = 0.0     # nominal dispatch bias (your model units)

    # --- Command limits ---
    Wref_min: float = 0.98
    Wref_max: float = 1.02

    Pref_min: float = -1.0     # allow reducing dispatch if needed
    Pref_max: float =  1.0

    # --- Control objectives ---
    # If you have WPU available, use it; otherwise you can set these to 0 and rely on SMACH/OVERLOADED.
    WPU_target: float = 1.0
    WPU_deadband: float = 0.0015     # pu

    # Use SMACH as "loading" indicator (most robust for your diagrams)
    SMACH_limit: float = 1.50        # your screenshot shows 1.50 threshold; tune to your chosen rating
    SMACH_margin: float = 0.05       # start protecting before the hard limit (pu)

    # Voltage guard (optional, gentle)
    Vmin_pu: float = 0.92            # if voltage dips below this, avoid aggressive Pref increases
    Vsoft_pu: float = 0.96           # softer threshold

    # --- Control gains (supervisory nudges; NOT governor Kp/Ki) ---
    # How strongly to push Pref based on frequency/speed error (if WPU is available)
    k_pref_wpu: float = 2.0          # Pref units per pu error (small, because Pref is bias)

    # How strongly to push Wref based on sustained droop error
    k_wref_wpu: float = 0.2          # Wref units per pu error (very small)

    # --- Filtering and rate limits (key for degraded data) ---
    meas_lpf_tau: float = 0.15       # seconds; filter noisy/degraded measurements

    wref_rate_up: float = 0.01       # pu/sec
    wref_rate_down: float = 0.02     # pu/sec (allow faster downward correction)

    pref_rate_up: float = 0.20       # units/sec
    pref_rate_down: float = 0.30     # units/sec

    # --- Data-quality handling ---
    # If measurements are missing/stale too long, hold last commands (fail-soft)
    max_stale_s: float = 0.50

    # --- Protection interaction ---
    # When OVERLOADED triggers, you can either:
    #  - block and reset (hard safety), or
    #  - reduce Pref aggressively and keep running (soft ride-through).
    # Here we do soft first; hard if persistent.
    overload_soft_drop: float = 0.30     # Pref drop when overload=1
    overload_hard_after_s: float = 1.00  # if overload persists this long, block+reset

    # Pushbutton pulse width for RESETGEN
    reset_pulse_s: float = 0.10


# -----------------------------
# Main controller
# -----------------------------

class DGController:
    """
    Supervisory controller that nudges Wref and Pref and manages BLOCK/RESET.

    Core philosophy:
    - Keep generator within a safe envelope (SMACH margin, voltage, overload latch behavior).
    - Use slow, rate-limited setpoint shifts so behavior is realistic and stable.
    - If data degrades: filter more, move slower, and hold last good rather than thrash.
    """

    def __init__(self, cfg: DGControllerConfig):
        self.cfg = cfg

        # Filters for key measurements
        self._f_wpu = LowPass(cfg.meas_lpf_tau)
        self._f_smach = LowPass(cfg.meas_lpf_tau)
        self._f_vpu = LowPass(cfg.meas_lpf_tau)

        # Rate limiters for commands
        self._rl_wref = RateLimiter(cfg.wref_rate_up, cfg.wref_rate_down, y=cfg.Wref_base)
        self._rl_pref = RateLimiter(cfg.pref_rate_up, cfg.pref_rate_down, y=cfg.Pref_base)

        # State
        self._last_meas_ts: Optional[float] = None
        self._last_cmd: DGCommands = DGCommands(
            REM_BLOCKGEN=1,
            REM_Wref=cfg.Wref_base,
            REM_PREF=cfg.Pref_base,
            REM_RESETGEN=0,
        )

        self._overload_start_ts: Optional[float] = None
        self._reset_until_ts: float = 0.0

        # Mode flags you can toggle externally if you want
        self.enabled: bool = True
        self.remote_block_override: Optional[int] = None  # set to 0/1 to force

    # ---- public API ----

    def update(self, meas: DGMeasurements, dt: float, now: Optional[float] = None) -> DGCommands:
        """
        One control step.

        Parameters:
            meas : DGMeasurements
                Measurements (possibly degraded) from RTDS.
            dt : float
                Time step (seconds).
            now : float | None
                Timestamp; if None, uses time.time()

        Returns:
            DGCommands
        """
        if now is None:
            now = time.time()

        # If disabled, hold safe base
        if not self.enabled:
            return self._hold_safe_base(dt)

        # Data freshness
        meas_ok = self._measurements_present(meas)
        if meas_ok:
            self._last_meas_ts = now

        if self._is_stale(now):
            # Fail-soft: hold last commands (don’t chase ghosts)
            return self._hold_last(dt)

        # Filter key signals
        wpu = self._get_filtered(self._f_wpu, meas.WPU, dt)
        smach = self._get_filtered(self._f_smach, meas.SMACH, dt)
        vpu = self._get_filtered(self._f_vpu, meas.GENRMSPU, dt)

        # Start from base setpoints
        wref_cmd = self.cfg.Wref_base
        pref_cmd = self.cfg.Pref_base

        # 1) Soft envelope protection based on loading (SMACH) even if WPU missing
        if smach is not None:
            pref_cmd += self._pref_from_smach(smach)

        # 2) Frequency/speed support (only if WPU available)
        if wpu is not None:
            dp = self._pref_from_wpu(wpu)
            dw = self._wref_from_wpu(wpu)
            pref_cmd += dp
            wref_cmd += dw

        # 3) Voltage guardrail: if voltage is sagging, avoid pushing Pref up
        if vpu is not None:
            pref_cmd = self._apply_voltage_guard(pref_cmd, vpu)

        # 4) Overload latch handling
        block_cmd, reset_cmd, pref_cmd = self._handle_overload_logic(
            meas.OVERLOADED, pref_cmd, now
        )

        # 5) External override for block (if you want to force open/close testing)
        if self.remote_block_override is not None:
            block_cmd = int(self.remote_block_override)

        # Saturate + rate limit outputs
        wref_cmd = clamp(wref_cmd, self.cfg.Wref_min, self.cfg.Wref_max)
        pref_cmd = clamp(pref_cmd, self.cfg.Pref_min, self.cfg.Pref_max)

        wref_out = self._rl_wref.step(wref_cmd, dt)
        pref_out = self._rl_pref.step(pref_cmd, dt)

        out = DGCommands(
            REM_BLOCKGEN=int(block_cmd),
            REM_Wref=float(wref_out),
            REM_PREF=float(pref_out),
            REM_RESETGEN=int(reset_cmd),
        )
        self._last_cmd = out
        return out

    def request_reset(self, now: Optional[float] = None) -> None:
        """Request a RESETGEN pushbutton pulse (non-blocking)."""
        if now is None:
            now = time.time()
        self._reset_until_ts = max(self._reset_until_ts, now + self.cfg.reset_pulse_s)

    # ---- internals ----

    def _measurements_present(self, meas: DGMeasurements) -> bool:
        # You can decide what "present" means; SMACH is most critical here.
        return (meas.SMACH is not None) or (meas.WPU is not None) or (meas.OVERLOADED is not None)

    def _is_stale(self, now: float) -> bool:
        if self._last_meas_ts is None:
            return False  # first iteration, don’t instantly stale
        return (now - self._last_meas_ts) > self.cfg.max_stale_s

    def _get_filtered(self, filt: LowPass, x: Optional[float], dt: float) -> Optional[float]:
        if x is None:
            return None
        if not math.isfinite(x):
            return None
        return filt.step(float(x), dt)

    def _pref_from_smach(self, smach: float) -> float:
        """
        Keep SMACH away from limit:
        - If close to limit: start reducing Pref (shed mechanical request).
        - If far from limit: allow Pref to stay at base (no extra push).
        """
        limit = self.cfg.SMACH_limit
        margin = self.cfg.SMACH_margin
        soft = limit - margin

        if smach >= limit:
            # Strong pull-down
            # Drop proportional to exceedance; you can tune aggressiveness here.
            exceed = smach - limit
            return -0.5 - 2.0 * exceed
        elif smach >= soft:
            # Gentle pull-down as you approach the limit
            closeness = (smach - soft) / max(margin, 1e-6)  # 0..1
            return -0.2 * closeness
        else:
            return 0.0

    def _pref_from_wpu(self, wpu: float) -> float:
        """
        If frequency/speed drops, increase Pref bias to pick up load (within rate/limits).
        """
        e = self.cfg.WPU_target - wpu
        if abs(e) < self.cfg.WPU_deadband:
            return 0.0
        return self.cfg.k_pref_wpu * e

    def _wref_from_wpu(self, wpu: float) -> float:
        """
        Very small adjustment to Wref to reduce steady droop error.
        Keep this small—Pref is your main "muscle"; Wref is a gentle bias.
        """
        e = self.cfg.WPU_target - wpu
        if abs(e) < self.cfg.WPU_deadband:
            return 0.0
        return self.cfg.k_wref_wpu * e

    def _apply_voltage_guard(self, pref_cmd: float, vpu: float) -> float:
        """
        If voltage is low, avoid ramping Pref upward aggressively.
        This prevents "digging a deeper hole" where voltage dips and currents rise.
        """
        if vpu < self.cfg.Vmin_pu:
            # hard clamp: don't increase Pref beyond base
            return min(pref_cmd, self.cfg.Pref_base)
        if vpu < self.cfg.Vsoft_pu:
            # soften positive Pref
            if pref_cmd > self.cfg.Pref_base:
                # scale back the "extra" above base
                extra = pref_cmd - self.cfg.Pref_base
                scale = clamp((vpu - self.cfg.Vmin_pu) / (self.cfg.Vsoft_pu - self.cfg.Vmin_pu), 0.0, 1.0)
                return self.cfg.Pref_base + extra * scale
        return pref_cmd

    def _handle_overload_logic(
        self,
        overloaded: Optional[int],
        pref_cmd: float,
        now: float,
    ) -> Tuple[int, int, float]:
        """
        Soft ride-through first:
        - If OVERLOADED == 1: drop Pref a bit.
        - If persists longer than overload_hard_after_s: block + pulse reset.
        """
        block_cmd = 1
        reset_cmd = 0

        # Handle requested reset pulse
        if now < self._reset_until_ts:
            reset_cmd = 1

        if overloaded is None:
            # No overload info -> do nothing extra
            return block_cmd, reset_cmd, pref_cmd

        overloaded = 1 if int(overloaded) != 0 else 0

        if overloaded == 1:
            if self._overload_start_ts is None:
                self._overload_start_ts = now

            # Soft action: reduce Pref immediately
            pref_cmd -= abs(self.cfg.overload_soft_drop)

            # Hard action if persistent
            if (now - self._overload_start_ts) >= self.cfg.overload_hard_after_s:
                block_cmd = 0
                # pulse reset to clear latch logic (if your plant requires it)
                self.request_reset(now)
                if now < self._reset_until_ts:
                    reset_cmd = 1
        else:
            self._overload_start_ts = None

        return block_cmd, reset_cmd, pref_cmd

    def _hold_last(self, dt: float) -> DGCommands:
        """Hold last command values (still rate-limited internally)."""
        # Keep rate limiter state aligned with last cmd
        self._rl_wref.y = self._last_cmd.REM_Wref
        self._rl_pref.y = self._last_cmd.REM_PREF
        # End any reset pulse naturally
        if time.time() >= self._reset_until_ts:
            self._last_cmd.REM_RESETGEN = 0
        return self._last_cmd

    def _hold_safe_base(self, dt: float) -> DGCommands:
        """Return base setpoints and keep generator enabled."""
        wref_out = self._rl_wref.step(self.cfg.Wref_base, dt)
        pref_out = self._rl_pref.step(self.cfg.Pref_base, dt)
        reset_cmd = 1 if time.time() < self._reset_until_ts else 0
        out = DGCommands(
            REM_BLOCKGEN=1,
            REM_Wref=wref_out,
            REM_PREF=pref_out,
            REM_RESETGEN=reset_cmd,
        )
        self._last_cmd = out
        return out


# -----------------------------
# Convenience: mapping helpers
# -----------------------------

def meas_from_dict(d: Dict[str, object]) -> DGMeasurements:
    """
    Build measurements from a dict coming from your comms layer.
    Expected keys (based on your Channel 3 OUT list):
      NewDataSeq_3_, ReadyToSend_3_, SocketOverflow_3_, InvalidMsg_3_,
      PGEN, QGEN, BRKGEN, PMACH, QMACH, SMACH, GENRMSPU, OVERLOADED
    plus optional WPU if you add it later.
    """
    return DGMeasurements(
        NewDataSeq=_maybe_int(d.get("NewDataSeq_3_")),
        ReadyToSend=_maybe_int(d.get("ReadyToSend_3_")),
        SocketOverflow=_maybe_int(d.get("SocketOverflow_3_")),
        InvalidMsg=_maybe_int(d.get("InvalidMsg_3_")),
        PGEN=_maybe_float(d.get("PGEN")),
        QGEN=_maybe_float(d.get("QGEN")),
        BRKGEN=_maybe_int(d.get("BRKGEN")),
        PMACH=_maybe_float(d.get("PMACH")),
        QMACH=_maybe_float(d.get("QMACH")),
        SMACH=_maybe_float(d.get("SMACH")),
        GENRMSPU=_maybe_float(d.get("GENRMSPU")),
        OVERLOADED=_maybe_int(d.get("OVERLOADED")),
        WPU=_maybe_float(d.get("WPU")),  # optional
    )

def cmds_to_dict(cmds: DGCommands) -> Dict[str, object]:
    """
    Dict matching your Channel 3 IN list (Python -> RTDS):
      REM_BLOCKGEN (Int)
      REM_Wref     (Float)
      REM_PREF     (Float)
      REM_RESETGEN (Int)
    """
    return {
        "REM_BLOCKGEN": int(cmds.REM_BLOCKGEN),
        "REM_Wref": float(cmds.REM_Wref),
        "REM_PREF": float(cmds.REM_PREF),
        "REM_RESETGEN": int(cmds.REM_RESETGEN),
    }

def _maybe_float(x: object) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if not math.isfinite(v):
            return None
        return v
    except Exception:
        return None

def _maybe_int(x: object) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


# -----------------------------
# Minimal example loop (no sockets)
# -----------------------------
if __name__ == "__main__":
    cfg = DGControllerConfig(
        # Keep your screenshot default as starting point:
        SMACH_limit=1.50,
        Wref_base=1.0,
        Pref_base=0.0,
    )
    ctrl = DGController(cfg)

    # Fake stream (replace with your GTNET read + degradation module)
    dt = 0.05
    smach = 1.0
    wpu = 1.0
    vpu = 1.0

    for k in range(200):
        # pretend load is increasing
        smach += 0.003
        wpu -= 0.0008  # droop-like dip
        vpu -= 0.0005

        meas = DGMeasurements(SMACH=smach, WPU=wpu, GENRMSPU=vpu, OVERLOADED=1 if smach > 1.5 else 0)
        cmds = ctrl.update(meas, dt)

        print(
            f"k={k:03d}  SMACH={smach:.3f}  WPU={wpu:.4f}  V={vpu:.3f}  "
            f"-> BLOCK={cmds.REM_BLOCKGEN} Wref={cmds.REM_Wref:.4f} Pref={cmds.REM_PREF:.3f} Reset={cmds.REM_RESETGEN}"
        )
        time.sleep(dt)
