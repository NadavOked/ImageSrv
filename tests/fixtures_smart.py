"""smartctl outputs of the college's disks, for #1859.

**Not captured from the lab** (no SSH to the lab in this task): built to the exact
format smartctl 7.3 prints for these models -- the attribute set of a Samsung 860
EVO and of a Samsung PM871 (MZ7LN256, the HP 8100s' OEM disk) as smartmontools'
drivedb names them, and NVMe as nvmeprint.cpp prints it. Replace them with real
captures (`smartctl -H -A /dev/sdX` on the cloner) when the lab is available --
the tests then read the same columns.
"""

HEADER = (
    "smartctl 7.3 2022-02-28 r5338 [x86_64-linux-6.1.0-26-amd64] (local build)\n"
    "Copyright (C) 2002-22, Bruce Allen, Christian Franke, www.smartmontools.org\n\n"
    "=== START OF READ SMART DATA SECTION ===\n"
)
TABLE = (
    "SMART Attributes Data Structure revision number: 1\n"
    "Vendor Specific SMART Attributes with Thresholds:\n"
    "ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE\n"
)


def row(i: int, name: str, flag: str, value: int, worst: int, thresh: int, kind: str, raw: str) -> str:
    return (f"{i:>3} {name:<23} {flag}   {value:03d}   {worst:03d}   {thresh:03d}    "
            f"{kind:<9} Always       -       {raw}\n")


def evo860(*, health="PASSED", realloc=0, wear_value=99, wear_raw=5, runtime_bad=0, uncorr=0,
           crc=0, temp=30) -> str:
    """Samsung SSD 860 EVO 500GB."""
    return (HEADER + f"SMART overall-health self-assessment test result: {health}\n\n" + TABLE
            + row(5, "Reallocated_Sector_Ct", "0x0033", 100, 100, 10, "Pre-fail", str(realloc))
            + row(9, "Power_On_Hours", "0x0032", 97, 97, 0, "Old_age", "12871")
            + row(12, "Power_Cycle_Count", "0x0032", 99, 99, 0, "Old_age", "1402")
            + row(177, "Wear_Leveling_Count", "0x0013", wear_value, wear_value, 0, "Pre-fail", str(wear_raw))
            + row(179, "Used_Rsvd_Blk_Cnt_Tot", "0x0013", 100, 100, 10, "Pre-fail", "0")
            + row(181, "Program_Fail_Cnt_Total", "0x0032", 100, 100, 10, "Old_age", "0")
            + row(182, "Erase_Fail_Count_Total", "0x0032", 100, 100, 10, "Old_age", "0")
            + row(183, "Runtime_Bad_Block", "0x0013", 100, 100, 10, "Pre-fail", str(runtime_bad))
            + row(187, "Uncorrectable_Error_Cnt", "0x0032", 100, 100, 0, "Old_age", str(uncorr))
            + row(190, "Airflow_Temperature_Cel", "0x0032", 100 - temp, 47, 0, "Old_age", str(temp))
            + row(195, "ECC_Error_Rate", "0x001a", 200, 200, 0, "Old_age", "0")
            + row(199, "CRC_Error_Count", "0x003e", 100, 100, 0, "Old_age", str(crc))
            + row(235, "POR_Recovery_Count", "0x0012", 99, 99, 0, "Old_age", "87")
            + row(241, "Total_LBAs_Written", "0x0032", 99, 99, 0, "Old_age", "28817154403"))


def mz7ln256(*, realloc=0, uncorr=0, crc=0, temp=34) -> str:
    """SAMSUNG MZ7LN256HCHP-000H1 (PM871, the HP OEM disk)."""
    return (HEADER + "SMART overall-health self-assessment test result: PASSED\n\n" + TABLE
            + row(5, "Reallocated_Sector_Ct", "0x0033", 100, 100, 10, "Pre-fail", str(realloc))
            + row(9, "Power_On_Hours", "0x0032", 92, 92, 0, "Old_age", "38212")
            + row(12, "Power_Cycle_Count", "0x0032", 97, 97, 0, "Old_age", "2911")
            + row(177, "Wear_Leveling_Count", "0x0013", 97, 97, 5, "Pre-fail", "46")
            + row(178, "Used_Rsvd_Blk_Cnt_Chip", "0x0013", 100, 100, 10, "Pre-fail", "0")
            + row(181, "Program_Fail_Cnt_Total", "0x0032", 100, 100, 10, "Old_age", "0")
            + row(182, "Erase_Fail_Count_Total", "0x0032", 100, 100, 10, "Old_age", "0")
            + row(183, "Runtime_Bad_Block", "0x0013", 100, 100, 10, "Pre-fail", "0")
            + row(187, "Uncorrectable_Error_Cnt", "0x0032", 100, 100, 0, "Old_age", str(uncorr))
            + row(190, "Airflow_Temperature_Cel", "0x0032", 100 - temp, 45, 0, "Old_age", str(temp))
            + row(195, "ECC_Error_Rate", "0x001a", 200, 200, 0, "Old_age", "0")
            + row(199, "CRC_Error_Count", "0x003e", 100, 100, 0, "Old_age", str(crc))
            + row(235, "POR_Recovery_Count", "0x0012", 99, 99, 0, "Old_age", "201")
            + row(241, "Total_LBAs_Written", "0x0032", 99, 99, 0, "Old_age", "17733821947"))


def nvme(*, used=3, spare=100, spare_threshold=10, media_errors=0, critical="0x00", temp=38) -> str:
    """smartctl 7.x on an NVMe disk (nvmeprint.cpp). There is no NVMe in the lab."""
    failed = critical != "0x00"
    return (
        HEADER.replace("READ SMART DATA", "SMART DATA")
        + "SMART overall-health self-assessment test result: "
        + ("FAILED!\n- media has been placed in read only mode\n" if failed else "PASSED\n") + "\n"
        "SMART/Health Information (NVMe Log 0x02)\n"
        f"Critical Warning:                   {critical}\n"
        f"Temperature:                        {temp} Celsius\n"
        f"Available Spare:                    {spare}%\n"
        f"Available Spare Threshold:          {spare_threshold}%\n"
        f"Percentage Used:                    {used}%\n"
        "Data Units Read:                    12,345,678 [6.32 TB]\n"
        "Data Units Written:                 9,876,543 [5.05 TB]\n"
        "Power Cycles:                       784\n"
        "Power On Hours:                     5,432\n"
        "Unsafe Shutdowns:                   61\n"
        f"Media and Data Integrity Errors:    {media_errors:,}\n"
        "Error Information Log Entries:      12\n"
    )


#: a disk with no SMART, as `smartctl -H -A` answers (ataprint.cpp's error path, not the
#: aligned `-i` line): smartctl opens it and says so -- "not checked", never "fine".
NO_SMART = (HEADER.replace("=== START OF READ SMART DATA SECTION ===\n", "")
            + "SMART support is: Unavailable - device lacks SMART capability.\n"
            "A mandatory SMART command failed: exiting. To continue, add one or more '-T permissive' options.\n")
