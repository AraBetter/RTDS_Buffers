# channel_specs.py
# -----------------------------------------------------------------------------
# Central place to define GTNET-SKT MULTI channel layouts (ports, names, formats)
# for your RTDS Microgrid project.
#
# This file defines ONLY "specs" (no sockets, no threads). It is imported by
# your application code that instantiates GtnetChannel(spec).
#
#
#   Channel 1 (port 7000) - PCC: 13 telemetry words, 4 command words
#   Channel 2 (port 7001) - BESS: 27 telemetry words, 3 command words
#   Channel 3 (port 7002) - DG: 14 telemetry words, 4 command words
#   Channel 4 (port 7003) - LOAD: 8 telemetry words, 2 command words
#
# IMPORTANT:
# - The order of names MUST match the word order in the GTNET-SKT MULTI block.
# - Types MUST match (Int vs Float) exactly.
# - Endianness: use ">" (big-endian) to match your existing working scripts.
# -----------------------------------------------------------------------------

from __future__ import annotations

from typing import Dict

from Comms.gtnet_channel import ChannelSpec

# -----------------------------------------------------------------------------#
# Default RTDS/GTNET endpoint
# -----------------------------------------------------------------------------#
GTNET_IP_DEFAULT = "172.24.4.3"

# -----------------------------------------------------------------------------#
# Channel 1 (PCC / Grid + Fault / Mode)
# Port: 7000
#
# RTDS -> Python (12):
#   0  NewDataFlag_1      Int
#   1  NewDataSeq_1       Int
#   2  ReadyToSend_1      Int
#   3  SocketOverflow_1   Int
#   4  InvalidMsg_1       Int
#   5  MODE              Int
#   6  PGRID             Float
#   7  QGRID             Float
#   8  N650RMSPU          Float
#   9  IGRIDA            Float
#   10 IGRIDB            Float
#   11 IGRIDC            Float
#   12 GRID              Int
#
# Python -> RTDS (4):
#   0  REM_GRID          Int
#   1  REM_LGFLTx        Int
#   2  REM_LGFTIMEx      Float
#   3  REM_LGFLTxType     Int
# -----------------------------------------------------------------------------#
CH1_MEAS_NAMES = [
    "NewDataFlag_1_",
    "NewDataSeq_1_",
    "ReadyToSend_1_",
    "SocketOverflow_1_",
    "InvalidMsg_1_",
    "MODE",
    "PGRID",
    "QGRID",
    "N650RMSPU",
    "IGRIDA",
    "IGRIDB",
    "IGRIDC",
    "GRID",
]
CH1_MEAS_FMT = ">iiiiiiffffffi"  # 6x int32 + 6x float32 + 1x int32   (big-endian)

CH1_CMD_NAMES = [
    "REM_GRID",
    "REM_LGFLTx",
    "REM_LGFTIMEx",
    "REM_LGFLTxType",
]
CH1_CMD_FMT = ">ii fi".replace(" ", "")  # -> ">iifi"  (int, int, float, int)

CHANNEL_1 = ChannelSpec(
    name="CH1",
    ip=GTNET_IP_DEFAULT,
    port=7000,
    meas_names=CH1_MEAS_NAMES,
    meas_fmt=CH1_MEAS_FMT,
    cmd_names=CH1_CMD_NAMES,
    cmd_fmt=CH1_CMD_FMT,
    cmd_types=["int", "int", "float", "int"],
    ready_to_send_name="ReadyToSend_1_",
    require_ready_to_send=True,
)

# -----------------------------------------------------------------------------#
# Channel 2 (BESS)
# Port: 7001
#
# RTDS -> Python (27):
#   0  NewDataFlag_2      Int
#   1  NewDataSeq_2       Int
#   2  ReadyToSend_2      Int
#   3  SocketOverflow_2   Int
#   4  InvalidMsg_2       Int
#   5  BRK1island         Int
#   6  OMEGA            Float
#   7  THETA            Float
#   8  ERR_5            Float
#   9  V_DETECT           Int
#   10 F_DETECT           Int
#   11 LOCKSIG            Int
#   12 INITx              Int
#   13 block            Float
#   14 Pmeas            Float
#   15 Qmeas            Float
#   16 BRK1               Int
#   17 SOC1             Float
#   18 VLOADRMS         Float
#   19 Isqref_V3        Float
#   20 Isdref_V3        Float
#   21 Perr             Float
#   22 Qerr             Float
#   23 VAave            Float
#   24 VBave            Float
#   25 VCave            Float
#   26 IDCave           Float
#
#
# Python -> RTDS (5):
#   0  REM_Preftest     Float
#   1  REM_Qreftest     Float
#   2  REM_BLOCK        Int
#   3  REM_CHKRESET     Int
#   4  REM_BESSBRK      Int
# -----------------------------------------------------------------------------#
CH2_MEAS_NAMES = [
    "NewDataFlag_2_",
    "NewDataSeq_2_",
    "ReadyToSend_2_",
    "SocketOverflow_2_",
    "InvalidMsg_2_",
    "BRK1island",
    "OMEGA",
    "THETA",
    "ERR_5",
    "V_DETECT",
    "F_DETECT",
    "LOCKSIG",
    "INITx",
    "block",
    "Pmeas",
    "Qmeas",
    "BRK1",
    "SOC1",
    "VLOADRMS",
    "Isqref_V3",
    "Isdref_V3",
    "Perr",
    "Qerr",
    "VAave",
    "VBave",
    "VCave",
    "IDCave",
]
CH2_MEAS_FMT = ">iiiiiifffiiiifffiffffffffff"  # 6x int32 + 3x float32 + 4x int32 +3x float32 + 1x int32 + 10x float32 (big-endian)

CH2_CMD_NAMES = [
    "REM_Preftest",
    "REM_Qreftest",
    "REM_BLOCK",
    "REM_CHKRESET",
    "REM_BESSBRK",
]
CH2_CMD_FMT =">ffiii"  # 2x float32 + 3x int32

CHANNEL_2 = ChannelSpec(
    name="CH2",
    ip=GTNET_IP_DEFAULT,
    port=7001,
    meas_names=CH2_MEAS_NAMES,
    meas_fmt=CH2_MEAS_FMT,
    cmd_names=CH2_CMD_NAMES,
    cmd_fmt=CH2_CMD_FMT,
    cmd_types=["float", "float", "int", "int", "int"],
    ready_to_send_name="ReadyToSend_2_",
    require_ready_to_send=True,
)

# -----------------------------------------------------------------------------#
# Channel 3 (DIESEL GENERATOR)
# Port: 7002
#
# RTDS -> Python (13):
#   0  NewDataFlag_3      Int
#   1  NewDataSeq_3       Int
#   2  ReadyToSend_3      Int
#   3  SocketOverflow_3   Int
#   4  InvalidMsg_3       Int
#   5  PGEN             Float
#   6  QGEN             Float
#   7  BRKGEN             Int
#   8  PMACH            Float
#   9  QMACH            Float
#   10 SMACH            Float
#   11 GENRMSPU         Float
#   12 OVERLOADED         Int
#   13 WPU              Float
#
#
# Python -> RTDS (4):
#   0  REM_BLOCKGEN       Int
#   1  REM_Wref         Float
#   2  REM_PREF         Float
#   3  REM_RESETGEN       Int

# -----------------------------------------------------------------------------#
CH3_MEAS_NAMES = [
    "NewDataFlag_3_",
    "NewDataSeq_3_",
    "ReadyToSend_3_",
    "SocketOverflow_3_",
    "InvalidMsg_3_",
    "PGEN",
    "QGEN",
    "BRKGEN",
    "PMACH",
    "QMACH",
    "SMACH",
    "GENRMSPU",
    "OVERLOADED",
    "WPU",
    "W_DETECTED",
]
CH3_MEAS_FMT = ">iiiiiffiffffifi"  # 5x int32 + 2x float32 + 1x int32 +4x float32 + 1x int32 + 1x float32 + 1x int32 (big-endian)

CH3_CMD_NAMES = [
    "REM_BLOCKGEN",
    "REM_Wref",
    "REM_PREF",
    "REM_RESETGEN",
]
CH3_CMD_FMT =">iffi"  # 1x int32 + 2x float32 + 1x int32

CHANNEL_3 = ChannelSpec(
    name="CH3",
    ip=GTNET_IP_DEFAULT,
    port=7002,
    meas_names=CH3_MEAS_NAMES,
    meas_fmt=CH3_MEAS_FMT,
    cmd_names=CH3_CMD_NAMES,
    cmd_fmt=CH3_CMD_FMT,
    cmd_types=["int","float", "float","int"],
    ready_to_send_name="ReadyToSend_3_",
    require_ready_to_send=True,
)

# -----------------------------------------------------------------------------#
# Channel 4 (Load / General)
# Port: 7003
#
# RTDS -> Python (8):
#   0 NewDataFlag_4       Int
#   1 NewDataSeq_4        Int
#   2 ReadyToSend_4       Int
#   3 SocketOverflow_4    Int
#   4 InvalidMsg_4        Int
#   5 PLOAD680            Float
#   6 QLOAD680            Float
#   7 N680RMSPU           Float
#
# Python -> RTDS (2):
#   0 REM_PLOAD           Float
#   1 REM_QLOAD           Float
# -----------------------------------------------------------------------------#
CH4_MEAS_NAMES = [
    "NewDataFlag_4_",
    "NewDataSeq_4_",
    "ReadyToSend_4_",
    "SocketOverflow_4_",
    "InvalidMsg_4_",
    "PLOAD680",
    "QLOAD680",
    "N680RMSPU",
]
CH4_MEAS_FMT = ">iiiiifff"  # 5x int32 + 3x float32

CH4_CMD_NAMES = [
    "REM_PLOAD",
    "REM_QLOAD",
]
CH4_CMD_FMT = ">ff"  # 2x float32

CHANNEL_4 = ChannelSpec(
    name="CH4",
    ip=GTNET_IP_DEFAULT,
    port=7003,
    meas_names=CH4_MEAS_NAMES,
    meas_fmt=CH4_MEAS_FMT,
    cmd_names=CH4_CMD_NAMES,
    cmd_fmt=CH4_CMD_FMT,
    cmd_types=["float", "float"],
    ready_to_send_name="ReadyToSend_4_",
    require_ready_to_send=True,
)

# -----------------------------------------------------------------------------#
# Export a dictionary for convenience
# -----------------------------------------------------------------------------#
CHANNEL_SPECS: Dict[str, ChannelSpec] = {
    "CH1": CHANNEL_1,
    "CH2": CHANNEL_2,
    "CH3": CHANNEL_3,
    "CH4": CHANNEL_4,
}
