#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <libei.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <systemd/sd-bus.h>
#include <time.h>
#include <unistd.h>

#define EIS_CAPABILITIES (EI_DEVICE_CAP_POINTER | EI_DEVICE_CAP_POINTER_ABSOLUTE | EI_DEVICE_CAP_BUTTON)
#define SWEEP_INTERVAL_NS 1000000
#define SWEEP_WIDTH 40

struct sweep
{
    struct ei_device *device;
    int position;
    int direction;
    uint64_t next;
};

static volatile sig_atomic_t terminated;

static void on_terminate(int signal_number)
{
    (void)signal_number;
    terminated = 1;
}

static int open_eis_socket(sd_bus *bus)
{
    sd_bus_message *reply = NULL;
    sd_bus_error error = SD_BUS_ERROR_NULL;
    int fd, cookie, socket_fd = -1, result;

    result = sd_bus_call_method(bus, "org.kde.KWin", "/org/kde/KWin/EIS/RemoteDesktop",
                                "org.kde.KWin.EIS.RemoteDesktop", "connectToEIS", &error, &reply,
                                "i", (int)EIS_CAPABILITIES);
    if (result < 0)
    {
        fprintf(stderr, "eis-pointer: connectToEIS failed: %s\n", error.message ? error.message : strerror(-result));
        sd_bus_error_free(&error);
        return -1;
    }
    result = sd_bus_message_read(reply, "hi", &fd, &cookie);
    if (result < 0) fprintf(stderr, "eis-pointer: unexpected connectToEIS reply: %s\n", strerror(-result));
    else socket_fd = fcntl(fd, F_DUPFD_CLOEXEC, 3);
    sd_bus_message_unref(reply);
    return socket_fd;
}

static bool create_ready_file(const char *path)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_CLOEXEC, 0600);

    if (fd < 0)
    {
        fprintf(stderr, "eis-pointer: cannot create %s: %s\n", path, strerror(errno));
        return false;
    }
    close(fd);
    return true;
}

static bool is_pointer(struct ei_device *device)
{
    return ei_device_has_capability(device, EI_DEVICE_CAP_POINTER) ||
           ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE);
}

static uint64_t now_ns(void)
{
    struct timespec now;

    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}

static void sweep_step(struct ei *ei, struct sweep *sweep)
{
    if (!sweep->device || now_ns() < sweep->next) return;
    if (sweep->position == SWEEP_WIDTH / 2 || sweep->position == -SWEEP_WIDTH / 2) sweep->direction = -sweep->direction;
    sweep->position += sweep->direction;
    ei_device_pointer_motion(sweep->device, sweep->direction, 0);
    ei_device_frame(sweep->device, ei_now(ei));
    sweep->next = now_ns() + SWEEP_INTERVAL_NS;
}

static bool handle_event(struct ei_event *event, const char *ready_file, bool *ready, struct sweep *sweep)
{
    struct ei_device *device;

    switch (ei_event_get_type(event))
    {
    case EI_EVENT_DISCONNECT:
        fprintf(stderr, "eis-pointer: the compositor closed the EIS connection\n");
        return false;
    case EI_EVENT_SEAT_ADDED:
        ei_seat_bind_capabilities(ei_event_get_seat(event), EI_DEVICE_CAP_POINTER,
                                  EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, NULL);
        break;
    case EI_EVENT_DEVICE_RESUMED:
        device = ei_event_get_device(event);
        ei_device_start_emulating(device, 0);
        if (sweep && !sweep->device && ei_device_has_capability(device, EI_DEVICE_CAP_POINTER)) sweep->device = device;
        if (!*ready && is_pointer(device)) *ready = create_ready_file(ready_file);
        break;
    default:
        break;
    }
    return true;
}

static bool drain_events(struct ei *ei, const char *ready_file, bool *ready, struct sweep *sweep)
{
    struct ei_event *event;
    bool alive = true;

    while (alive && (event = ei_get_event(ei)))
    {
        alive = handle_event(event, ready_file, ready, sweep);
        ei_event_unref(event);
    }
    return alive;
}

static int run(struct ei *ei, const char *ready_file, struct sweep *sweep, const sigset_t *waiting_mask)
{
    struct pollfd poll_fd = {.fd = ei_get_fd(ei), .events = POLLIN};
    struct timespec interval = {.tv_nsec = sweep ? SWEEP_INTERVAL_NS / 4 : 100000000};
    bool ready = false;

    while (!terminated)
    {
        if (ppoll(&poll_fd, 1, &interval, waiting_mask) < 0)
        {
            if (errno == EINTR) continue;
            fprintf(stderr, "eis-pointer: ppoll failed: %s\n", strerror(errno));
            return 1;
        }
        ei_dispatch(ei);
        if (!drain_events(ei, ready_file, &ready, sweep)) return 1;
        if (sweep) sweep_step(ei, sweep);
        ei_dispatch(ei);
    }
    return 0;
}

int main(int argc, char **argv)
{
    struct sigaction action = {.sa_handler = on_terminate};
    sigset_t blocked, original;
    struct sweep sweep = {.direction = 1};
    struct ei *ei;
    sd_bus *bus = NULL;
    int socket_fd, status;

    if (argc != 2 && (argc != 3 || strcmp(argv[2], "sweep")))
    {
        fprintf(stderr, "usage: eis-pointer <file to create once a pointer is ready> [sweep]\n");
        return 2;
    }
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGTERM);
    sigaddset(&blocked, SIGINT);
    sigprocmask(SIG_BLOCK, &blocked, &original);

    if (sd_bus_open_user(&bus) < 0)
    {
        fprintf(stderr, "eis-pointer: cannot open the session bus\n");
        return 1;
    }
    socket_fd = open_eis_socket(bus);
    if (socket_fd < 0) return 1;

    ei = ei_new_sender(NULL);
    if (!ei)
    {
        fprintf(stderr, "eis-pointer: cannot create the libei context\n");
        return 1;
    }
    ei_configure_name(ei, "eis-pointer");
    if (ei_setup_backend_fd(ei, socket_fd) != 0)
    {
        fprintf(stderr, "eis-pointer: ei_setup_backend_fd failed\n");
        return 1;
    }

    status = run(ei, argv[1], argc == 3 ? &sweep : NULL, &original);
    ei_unref(ei);
    sd_bus_unref(bus);
    return status;
}
