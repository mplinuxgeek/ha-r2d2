class DriveFlags:
    FORWARD   = 0x00
    BACKWARD  = 0x01
    TURBO     = 0x02
    FAST_TURN = 0x04

class LegAction:
    STOP   = 0x00
    TRIPOD = 0x01
    BIPOD  = 0x02
    WADDLE = 0x03

class AudioMode:
    PLAY_IMMEDIATELY         = 0x00
    PLAY_ONLY_IF_NOT_PLAYING = 0x01
    PLAY_AFTER_CURRENT       = 0x02

ANIMATIONS = {
    # Emotes
    "alarm":         7,
    "angry":         8,
    "attention":     9,
    "frustrated":    10,
    "drive":         11,
    "excited":       12,
    "search":        13,
    "short_circuit": 14,
    "laugh":         15,
    "no":            16,
    "retreat":       17,
    "fiery":         18,
    "understood":    19,
    "yes":           21,
    "scan":          22,
    "surprised":     24,
    # Idle
    "idle_1": 25,
    "idle_2": 26,
    "idle_3": 27,
    # WWM (When Will Meet)
    "wwm_angry":       31,
    "wwm_anxious":     32,
    "wwm_bow":         33,
    "wwm_concern":     34,
    "wwm_curious":     35,
    "wwm_double_take": 36,
    "wwm_excited":     37,
    "wwm_fiery":       38,
    "wwm_frustrated":  39,
    "wwm_happy":       40,
    "wwm_jittery":     41,
    "wwm_laugh":       42,
    "wwm_long_shake":  43,
    "wwm_no":          44,
    "wwm_ominous":     45,
    "wwm_relieved":    46,
    "wwm_sad":         47,
    "wwm_scared":      48,
    "wwm_shake":       49,
    "wwm_surprised":   50,
    "wwm_taunting":    51,
    "wwm_whisper":     52,
    "wwm_yelling":     53,
    "wwm_yoohoo":      54,
    "motor":           55,
    # Aliases
    "happy": 40,
    "sad":   47,
}

AUDIO = {
    "fall":             1609,
    # Hits
    "hit_1": 1623, "hit_2": 1642, "hit_3": 1647, "hit_4": 1653,
    "hit_5": 1659, "hit_6": 1664, "hit_7": 1669, "hit_8": 1676,
    "hit_9": 1684, "hit_10": 1628,
    # Steps
    "step_1": 1690, "step_2": 1693, "step_3": 1696,
    "step_4": 1698, "step_5": 1700, "step_6": 1702,
    # Misc
    "access_panels":     1704,
    "annoyed":           1910,
    "burnout":           1915,
    "engage_hyperdrive": 2586,
    "head_spin":         2797,
    "scream":            3797,
    "scream_2":          3810,
    "short_out":         3825,
    # Alarms
    "alarm_1":  1737, "alarm_2":  1809, "alarm_3":  1821, "alarm_4":  1831,
    "alarm_5":  1835, "alarm_6":  1843, "alarm_7":  1858, "alarm_8":  1867,
    "alarm_9":  1893, "alarm_10": 1747, "alarm_12": 1756, "alarm_13": 1763,
    "alarm_14": 1771, "alarm_15": 1784, "alarm_16": 1791,
    # Chatty (selection)
    "chatty_1":  1950, "chatty_2":  2061, "chatty_3":  2174, "chatty_4":  2276,
    "chatty_5":  2399, "chatty_6":  2524, "chatty_7":  2562, "chatty_8":  2572,
    "chatty_9":  2579, "chatty_10": 1959,
    # Excited
    "excited_1": 2600, "excited_2": 2708, "excited_3": 2726, "excited_4": 2730,
    "excited_5": 2736, "excited_6": 2753, "excited_7": 2767, "excited_8": 2777,
    # Hey
    "hey_1": 2813, "hey_2": 2841, "hey_3": 2856, "hey_4": 2861,
    "hey_5": 2882, "hey_6": 2893, "hey_7": 2898, "hey_8": 2904,
    # Laugh
    "laugh_1": 2919, "laugh_2": 2935, "laugh_3": 2950, "laugh_4": 2955,
    # Negative
    "negative_1":  3101, "negative_2":  3172, "negative_3":  3251, "negative_4":  3258,
    "negative_5":  3263, "negative_6":  3268, "negative_7":  3274, "negative_8":  3282,
    "negative_9":  3291, "negative_10": 3111,
    # Positive
    "positive_1":  3302, "positive_2":  3394, "positive_3":  3439, "positive_4":  3446,
    "positive_5":  3449, "positive_6":  3454, "positive_7":  3460, "positive_8":  3471,
    "positive_9":  3478, "positive_10": 3309,
    # Sad
    "sad_1":  3484, "sad_2":  3608, "sad_3":  3686, "sad_4":  3693,
    "sad_5":  3703, "sad_6":  3739, "sad_7":  3755, "sad_8":  3782,
    "sad_9":  3790, "sad_10": 3495,
}
