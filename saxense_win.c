// SAxense Windows Port - DualSense Haptics over Bluetooth
// Based on SAxense by Sdore (https://apps.sdore.me/SAxense)
// Credit: Sdore for initial POC findings

#include <windows.h>
#include <hidsdi.h>
#include <setupapi.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#pragma comment(lib, "hid.lib")
#pragma comment(lib, "setupapi.lib")
#pragma comment(lib, "winmm.lib")

#define REPORT_SIZE   141
#define REPORT_ID     0x32
#define SAMPLE_SIZE   64
#define SAMPLE_RATE   3000
#define DS5_VID       0x054C
#define DS5_PID       0x0CE6

static uint32_t crc32(const uint8_t* data, size_t size) {
    uint32_t crc = ~0xEADA2D49;  // 0xA2 seed
    while (size--) {
        crc ^= *data++;
        for (unsigned i = 0; i < 8; i++)
            crc = ((crc >> 1) ^ (0xEDB88320 & -(crc & 1)));
    }
    return ~crc;
}

typedef struct __attribute__((packed)) {
    uint8_t pid_flags;  // pid(6) | unk(1) | sized(1)
    uint8_t length;
    uint8_t data[];
} packet_t;

static struct {
    uint8_t report_id;
    uint8_t tag_seq;
    uint8_t payload[REPORT_SIZE - 1 - 4];
    uint32_t crc;
} report;

static uint8_t *sample, *seq_byte;
static HANDLE hDevice = INVALID_HANDLE_VALUE;
static volatile int running = 1;

static HANDLE find_ds5_bt(void) {
    GUID hidGuid;
    HidD_GetHidGuid(&hidGuid);
    HDEVINFO devInfo = SetupDiGetClassDevs(&hidGuid, NULL, NULL,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    if (devInfo == INVALID_HANDLE_VALUE) return INVALID_HANDLE_VALUE;

    SP_DEVICE_INTERFACE_DATA ifData;
    ifData.cbSize = sizeof(ifData);

    for (DWORD i = 0; SetupDiEnumDeviceInterfaces(devInfo, NULL, &hidGuid, i, &ifData); i++) {
        DWORD reqSize;
        SetupDiGetDeviceInterfaceDetail(devInfo, &ifData, NULL, 0, &reqSize, NULL);
        PSP_DEVICE_INTERFACE_DETAIL_DATA detail = malloc(reqSize);
        detail->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA);
        if (!SetupDiGetDeviceInterfaceDetail(devInfo, &ifData, detail, reqSize, NULL, NULL)) {
            free(detail);
            continue;
        }

        HANDLE h = CreateFile(detail->DevicePath, GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        free(detail);

        if (h == INVALID_HANDLE_VALUE) continue;

        HIDD_ATTRIBUTES attrs;
        attrs.Size = sizeof(attrs);
        if (HidD_GetAttributes(h, &attrs)) {
            if (attrs.VendorID == DS5_VID && attrs.ProductID == DS5_PID) {
                // Check if BT (input report > 64 bytes)
                PHIDP_PREPARSED_DATA ppd;
                if (HidD_GetPreparsedData(h, &ppd)) {
                    HIDP_CAPS caps;
                    HidP_GetCaps(ppd, &caps);
                    HidD_FreePreparsedData(ppd);
                    if (caps.InputReportByteLength > 64) {
                        // BT device
                        SetupDiDestroyDeviceInfoList(devInfo);
                        return h;
                    }
                }
            }
        }
        CloseHandle(h);
    }
    SetupDiDestroyDeviceInfoList(devInfo);
    return INVALID_HANDLE_VALUE;
}

static void CALLBACK timer_proc(UINT uTimerID, UINT uMsg, DWORD_PTR dwUser,
                                  DWORD_PTR dw1, DWORD_PTR dw2) {
    // Read 64 bytes from stdin
    size_t n = fread(sample, 1, SAMPLE_SIZE, stdin);
    if (n == 0) { running = 0; return; }
    if (n < SAMPLE_SIZE) memset(sample + n, 0, SAMPLE_SIZE - n);

    (*seq_byte)++;

    // Calculate CRC
    report.crc = crc32((uint8_t*)&report, 1 + sizeof(report.payload));

    // Write report
    DWORD written;
    WriteFile(hDevice, &report, sizeof(report), &written, NULL);
}

int main(int argc, char* argv[]) {
    _setmode(_fileno(stdin), _O_BINARY);

    fprintf(stderr, "SAxense Windows Port\n");
    fprintf(stderr, "Finding DS5 (BT)...\n");

    hDevice = find_ds5_bt();
    if (hDevice == INVALID_HANDLE_VALUE) {
        fprintf(stderr, "No DS5 BT device found!\n");
        return 1;
    }
    fprintf(stderr, "DS5 found!\n");

    // Build report template (same as SAxense)
    memset(&report, 0, sizeof(report));
    report.report_id = REPORT_ID;
    report.tag_seq = 0;

    // Packet 0x11: control
    uint8_t pkt_0x11[] = {
        (0x11 & 0x3F) | (0 << 6) | (1 << 7),  // pid=0x11, sized=1
        7,  // length
        0xFE, 0, 0, 0, 0, 0xFF, 0  // data
    };

    // Packet 0x12: audio samples
    uint8_t pkt_0x12_hdr[] = {
        (0x12 & 0x3F) | (0 << 6) | (1 << 7),  // pid=0x12, sized=1
        SAMPLE_SIZE  // length
    };

    memcpy(report.payload, pkt_0x11, sizeof(pkt_0x11));
    memcpy(report.payload + sizeof(pkt_0x11), pkt_0x12_hdr, sizeof(pkt_0x12_hdr));

    seq_byte = &report.payload[6];  // pkt_0x11 data[5]
    sample = report.payload + sizeof(pkt_0x11) + sizeof(pkt_0x12_hdr);

    // Set timer resolution to 1ms
    timeBeginPeriod(1);

    // Timer interval: 10.67ms = SAMPLE_SIZE / (SAMPLE_RATE * 2) * 1000
    UINT interval_ms = (SAMPLE_SIZE * 1000) / (SAMPLE_RATE * 2);  // = 10
    fprintf(stderr, "Starting timer at %u ms interval (~%.1f Hz)\n",
            interval_ms, 1000.0 / interval_ms);

    MMRESULT timer = timeSetEvent(interval_ms, 1, timer_proc, 0, TIME_PERIODIC);
    if (!timer) {
        fprintf(stderr, "Failed to create timer!\n");
        return 1;
    }

    fprintf(stderr, "Playing... (Ctrl+C to stop)\n");

    while (running) {
        Sleep(100);
    }

    timeKillEvent(timer);
    timeEndPeriod(1);

    // Send silence
    memset(sample, 0, SAMPLE_SIZE);
    report.crc = crc32((uint8_t*)&report, 1 + sizeof(report.payload));
    DWORD written;
    WriteFile(hDevice, &report, sizeof(report), &written, NULL);

    CloseHandle(hDevice);
    fprintf(stderr, "Done.\n");
    return 0;
}
