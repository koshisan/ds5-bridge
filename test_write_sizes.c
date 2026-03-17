// Test which WriteFile sizes the BT HID stack accepts
#include <windows.h>
#include <hidsdi.h>
#include <setupapi.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#pragma comment(lib, "hid.lib")
#pragma comment(lib, "setupapi.lib")

#define DS5_VID 0x054C
#define DS5_PID 0x0CE6

int main() {
    GUID hidGuid;
    HidD_GetHidGuid(&hidGuid);
    HDEVINFO devInfo = SetupDiGetClassDevs(&hidGuid, NULL, NULL, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    SP_DEVICE_INTERFACE_DATA ifData;
    ifData.cbSize = sizeof(ifData);
    HANDLE hDev = INVALID_HANDLE_VALUE;

    for (DWORD i = 0; SetupDiEnumDeviceInterfaces(devInfo, NULL, &hidGuid, i, &ifData); i++) {
        DWORD reqSize;
        SetupDiGetDeviceInterfaceDetail(devInfo, &ifData, NULL, 0, &reqSize, NULL);
        PSP_DEVICE_INTERFACE_DETAIL_DATA detail = malloc(reqSize);
        detail->cbSize = sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA);
        SetupDiGetDeviceInterfaceDetail(devInfo, &ifData, detail, reqSize, NULL, NULL);
        HANDLE h = CreateFile(detail->DevicePath, GENERIC_READ|GENERIC_WRITE,
            FILE_SHARE_READ|FILE_SHARE_WRITE, NULL, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, NULL);
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
                if (caps.InputReportByteLength > 64) { hDev = h; break; }
            }
        }
        CloseHandle(h);
    }
    SetupDiDestroyDeviceInfoList(devInfo);
    if (hDev == INVALID_HANDLE_VALUE) { printf("No DS5 BT\n"); return 1; }

    OVERLAPPED ol = {0};
    ol.hEvent = CreateEvent(NULL, FALSE, FALSE, NULL);

    // Test sizes with report ID 0x32
    int sizes[] = {78, 141, 142, 143, 547, 548, 64, 48, 32, 0};
    for (int s = 0; sizes[s]; s++) {
        uint8_t* buf = (uint8_t*)calloc(sizes[s], 1);
        buf[0] = 0x32;
        DWORD written = 0;
        ResetEvent(ol.hEvent);
        BOOL ok = WriteFile(hDev, buf, sizes[s], &written, &ol);
        DWORD err = ok ? 0 : GetLastError();
        if (err == ERROR_IO_PENDING) {
            WaitForSingleObject(ol.hEvent, 500);
            GetOverlappedResult(hDev, &ol, &written, FALSE);
            err = 0;
            ok = TRUE;
        }
        printf("Size %3d: %s (err=%lu written=%lu)\n", sizes[s], ok?"OK":"FAIL", err, written);
        free(buf);
    }

    // Also test report ID 0x31 (standard output) for comparison
    printf("\n--- Report 0x31 ---\n");
    int sizes31[] = {78, 547, 48, 0};
    for (int s = 0; sizes31[s]; s++) {
        uint8_t* buf = (uint8_t*)calloc(sizes31[s], 1);
        buf[0] = 0x31;
        DWORD written = 0;
        ResetEvent(ol.hEvent);
        BOOL ok = WriteFile(hDev, buf, sizes31[s], &written, &ol);
        DWORD err = ok ? 0 : GetLastError();
        if (err == ERROR_IO_PENDING) {
            WaitForSingleObject(ol.hEvent, 500);
            GetOverlappedResult(hDev, &ol, &written, FALSE);
            err = 0;
            ok = TRUE;
        }
        printf("Size %3d: %s (err=%lu written=%lu)\n", sizes31[s], ok?"OK":"FAIL", err, written);
        free(buf);
    }

    CloseHandle(ol.hEvent);
    CloseHandle(hDev);
    return 0;
}
