// SAxense Windows Port v5 - persistent OVERLAPPED like hidapi
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

#define REPORT_SIZE   142
#define REPORT_ID     0x32
#define SAMPLE_SIZE   64
#define DS5_VID       0x054C
#define DS5_PID       0x0CE6

static uint32_t crc32_calc(const uint8_t* data, size_t size) {
    uint32_t crc = ~0xEADA2D49;
    while (size--) { crc ^= *data++; for (unsigned i = 0; i < 8; i++) crc = ((crc >> 1) ^ (0xEDB88320 & -(crc & 1))); }
    return ~crc;
}

static uint8_t report_buf[REPORT_SIZE];
static uint8_t *write_buf = NULL;  // padded to output_report_length
static uint8_t *sample_ptr, *seq_ptr;
static HANDLE hDevice = INVALID_HANDLE_VALUE;
static OVERLAPPED write_ol;  // persistent like hidapi
static FILE *input_file = NULL;
static volatile int running = 1;
static DWORD out_report_len = 0;

static HANDLE find_ds5_bt(void) {
    GUID hidGuid;
    HidD_GetHidGuid(&hidGuid);
    HDEVINFO devInfo = SetupDiGetClassDevs(&hidGuid, NULL, NULL, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    if (devInfo == INVALID_HANDLE_VALUE) return INVALID_HANDLE_VALUE;
    SP_DEVICE_INTERFACE_DATA ifData;
    ifData.cbSize = sizeof(ifData);
    for (DWORD i = 0; SetupDiEnumDeviceInterfaces(devInfo, NULL, &hidGuid, i, &ifData); i++) {
        DWORD reqSize;
        SetupDiGetDeviceInterfaceDetail(devInfo, &ifData, NULL, 0, &reqSize, NULL);
        PSP_DEVICE_INTERFACE_DETAIL_DATA detail = malloc(reqSize);
        detail->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA);
        if (!SetupDiGetDeviceInterfaceDetail(devInfo, &ifData, detail, reqSize, NULL, NULL)) { free(detail); continue; }
        HANDLE h = CreateFile(detail->DevicePath, GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, NULL);
        free(detail);
        if (h == INVALID_HANDLE_VALUE) continue;
        HIDD_ATTRIBUTES attrs;
        attrs.Size = sizeof(attrs);
        if (HidD_GetAttributes(h, &attrs) && attrs.VendorID == DS5_VID && attrs.ProductID == DS5_PID) {
            PHIDP_PREPARSED_DATA ppd;
            if (HidD_GetPreparsedData(h, &ppd)) {
                HIDP_CAPS caps;
                HidP_GetCaps(ppd, &caps);
                HidD_FreePreparsedData(ppd);
                if (caps.InputReportByteLength > 64) {
                    out_report_len = caps.OutputReportByteLength;
                    SetupDiDestroyDeviceInfoList(devInfo);
                    return h;
                }
            }
        }
        CloseHandle(h);
    }
    SetupDiDestroyDeviceInfoList(devInfo);
    return INVALID_HANDLE_VALUE;
}

static void do_write(void) {
    // Build BT frame: [0xA2][report_buf...] then write from offset 1
    // 0xA2 = BT HID transaction header (not actually sent, but needed in buffer for CRC)
    uint8_t bt_buf[1 + REPORT_SIZE];
    bt_buf[0] = 0xA2;  // BT header (used for CRC calc, skipped in write)
    memcpy(bt_buf + 1, report_buf, REPORT_SIZE);

    DWORD bytes_written = 0;
    // Write 141 bytes starting from report_buf (skip 0xA2 header)
    BOOL res = WriteFile(hDevice, report_buf, REPORT_SIZE, &bytes_written, &write_ol);
    if (!res) {
        DWORD err = GetLastError();
        if (err == ERROR_IO_PENDING) {
            WaitForSingleObject(write_ol.hEvent, 1000);
            GetOverlappedResult(hDevice, &write_ol, &bytes_written, FALSE);
        } else {
            static int errcnt = 0;
            if (errcnt++ < 10) fprintf(stderr, "WriteFile err=%lu written=%lu\n", err, bytes_written);
        }
    } else {
        static int ok_count = 0;
        if (ok_count++ < 3) fprintf(stderr, "WriteFile OK written=%lu\n", bytes_written);
    }
}

static void CALLBACK timer_proc(UINT uTimerID, UINT uMsg, DWORD_PTR dwUser, DWORD_PTR dw1, DWORD_PTR dw2) {
    size_t n = fread(sample_ptr, 1, SAMPLE_SIZE, input_file);
    if (n == 0) { running = 0; return; }
    if (n < SAMPLE_SIZE) memset(sample_ptr + n, 0, SAMPLE_SIZE - n);

    (*seq_ptr)++;

    uint32_t crc = crc32_calc(report_buf, REPORT_SIZE - 4);
    memcpy(report_buf + REPORT_SIZE - 4, &crc, 4);

    do_write();
}

int main(int argc, char* argv[]) {
    fprintf(stderr, "SAxense Windows Port v5\n");
    if (argc > 1) {
        input_file = fopen(argv[1], "rb");
        if (!input_file) { fprintf(stderr, "Cannot open: %s\n", argv[1]); return 1; }
    } else {
        _setmode(_fileno(stdin), 0x8000);
        input_file = stdin;
    }
    hDevice = find_ds5_bt();
    if (hDevice == INVALID_HANDLE_VALUE) { fprintf(stderr, "No DS5 BT!\n"); return 1; }
    fprintf(stderr, "DS5 found! OutLen=%lu\n", out_report_len);

    // Allocate padded write buffer (like hidapi)
    write_buf = (uint8_t*)calloc(out_report_len, 1);

    // Init persistent OVERLAPPED (like hidapi)
    memset(&write_ol, 0, sizeof(write_ol));
    write_ol.hEvent = CreateEvent(NULL, FALSE, FALSE, NULL);

    // Set input buffer size (like hidapi)
    HidD_SetNumInputBuffers(hDevice, 64);

    memset(report_buf, 0, sizeof(report_buf));
    report_buf[0] = REPORT_ID;
    report_buf[2] = (0x11 & 0x3F) | (1 << 7);
    report_buf[3] = 7;
    report_buf[4] = 0xFE;
    report_buf[10] = 0xFF;
    report_buf[11] = (0x12 & 0x3F) | (1 << 7);
    report_buf[12] = SAMPLE_SIZE;
    seq_ptr = &report_buf[10];
    sample_ptr = &report_buf[13];

    timeBeginPeriod(1);
    
    
    fprintf(stderr, "Playing (main thread loop)...\n");
    while (running) { timer_proc(0,0,0,0,0); Sleep(10); }
    
    timeEndPeriod(1);

    CloseHandle(write_ol.hEvent);
    CloseHandle(hDevice);
    free(write_buf);
    if (input_file != stdin) fclose(input_file);
    fprintf(stderr, "Done.\n");
    return 0;
}
