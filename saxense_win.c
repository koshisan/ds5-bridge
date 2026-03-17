// SAxense Windows Port v3 - correct BT report size
#include <windows.h>
#include <hidsdi.h>
#include <setupapi.h>
#include <stdio.h>
#include <fcntl.h>
#include <io.h>
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

static uint32_t crc32_calc(const uint8_t* data, size_t size) {
    uint32_t crc = ~0xEADA2D49;
    while (size--) {
        crc ^= *data++;
        for (unsigned i = 0; i < 8; i++)
            crc = ((crc >> 1) ^ (0xEDB88320 & -(crc & 1)));
    }
    return ~crc;
}

static uint8_t report_buf[REPORT_SIZE];
static uint8_t *sample_ptr;
static uint8_t *seq_ptr;
static HANDLE hDevice = INVALID_HANDLE_VALUE;
static FILE *input_file = NULL;
static volatile int running = 1;
static DWORD bt_report_size = 0;

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
            FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, NULL);
        free(detail);
        if (h == INVALID_HANDLE_VALUE) continue;

        HIDD_ATTRIBUTES attrs;
        attrs.Size = sizeof(attrs);
        if (HidD_GetAttributes(h, &attrs)) {
            if (attrs.VendorID == DS5_VID && attrs.ProductID == DS5_PID) {
                PHIDP_PREPARSED_DATA ppd;
                if (HidD_GetPreparsedData(h, &ppd)) {
                    HIDP_CAPS caps;
                    HidP_GetCaps(ppd, &caps);
                    HidD_FreePreparsedData(ppd);
                    fprintf(stderr, "  InputReportByteLength=%u OutputReportByteLength=%u\n",
                            caps.InputReportByteLength, caps.OutputReportByteLength);
                    if (caps.InputReportByteLength > 64) {
                        bt_report_size = caps.OutputReportByteLength;
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
    size_t n = fread(sample_ptr, 1, SAMPLE_SIZE, input_file);
    if (n == 0) { running = 0; return; }
    if (n < SAMPLE_SIZE) memset(sample_ptr + n, 0, SAMPLE_SIZE - n);

    (*seq_ptr)++;

    // CRC over report_id + payload (first 137 bytes)
    uint32_t crc = crc32_calc(report_buf, REPORT_SIZE - 4);
    memcpy(report_buf + REPORT_SIZE - 4, &crc, 4);

    // Send in a buffer sized to OutputReportByteLength
    OVERLAPPED ol = {0}; ol.hEvent = CreateEvent(NULL, TRUE, FALSE, NULL); DWORD written; BOOLEAN ok = WriteFile(hDevice, report_buf, REPORT_SIZE, &written, &ol); if (!ok && GetLastError() == ERROR_IO_PENDING) { WaitForSingleObject(ol.hEvent, 50); ok = TRUE; } CloseHandle(ol.hEvent);
    if (!ok) {
        static int errcnt = 0;
        if (errcnt++ < 5) fprintf(stderr, "WriteFile failed: %lu (size=%lu)\n", GetLastError(), bt_report_size);
    }
}

int main(int argc, char* argv[]) {
    fprintf(stderr, "SAxense Windows Port v3\n");

    if (argc > 1) {
        input_file = fopen(argv[1], "rb");
        if (!input_file) { fprintf(stderr, "Cannot open: %s\n", argv[1]); return 1; }
        fprintf(stderr, "Reading from: %s\n", argv[1]);
    } else {
        _setmode(_fileno(stdin), 0x8000);
        input_file = stdin;
        fprintf(stderr, "Reading from stdin\n");
    }

    fprintf(stderr, "Finding DS5 (BT)...\n");
    hDevice = find_ds5_bt();
    if (hDevice == INVALID_HANDLE_VALUE) { fprintf(stderr, "No DS5 BT!\n"); return 1; }
    fprintf(stderr, "DS5 found! OutputReportByteLength=%lu\n", bt_report_size);

    memset(report_buf, 0, sizeof(report_buf));
    report_buf[0] = REPORT_ID;
    report_buf[1] = 0;
    report_buf[2] = (0x11 & 0x3F) | (1 << 7);
    report_buf[3] = 7;
    report_buf[4] = 0xFE;
    report_buf[10] = 0xFF;
    report_buf[11] = (0x12 & 0x3F) | (1 << 7);
    report_buf[12] = SAMPLE_SIZE;

    seq_ptr = &report_buf[10];
    sample_ptr = &report_buf[13];

    timeBeginPeriod(1);
    UINT interval_ms = (SAMPLE_SIZE * 1000) / (SAMPLE_RATE * 2);
    fprintf(stderr, "Timer: %u ms\n", interval_ms);

    MMRESULT timer = timeSetEvent(interval_ms, 1, timer_proc, 0, TIME_PERIODIC);
    if (!timer) { fprintf(stderr, "Timer failed!\n"); return 1; }

    fprintf(stderr, "Playing...\n");
    while (running) Sleep(100);

    timeKillEvent(timer);
    timeEndPeriod(1);

    memset(sample_ptr, 0, SAMPLE_SIZE);
    uint32_t crc = crc32_calc(report_buf, REPORT_SIZE - 4);
    memcpy(report_buf + REPORT_SIZE - 4, &crc, 4);
    { DWORD w; WriteFile(hDevice, report_buf, REPORT_SIZE, &w, NULL); }

    CloseHandle(hDevice);
    if (input_file != stdin) fclose(input_file);
    fprintf(stderr, "Done.\n");
    return 0;
}
