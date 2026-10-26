#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

extern char **environ;

typedef struct {
    char *data;
    size_t len, cap;
} buf_t;

static void put(buf_t *b, const void *p, size_t n) {
    if (b->len + n > b->cap) {
        b->cap = (b->len + n) * 2 + 4096;
        b->data = realloc(b->data, b->cap);
        if (!b->data) _exit(2);
    }
    memcpy(b->data + b->len, p, n);
    b->len += n;
}

static void put_field(buf_t *b, const char *s) { put(b, s, strlen(s) + 1); }

static void put_num(buf_t *b, size_t n) {
    char tmp[32];
    snprintf(tmp, sizeof tmp, "%zu", n);
    put_field(b, tmp);
}

static void fallback(int argc, char **argv, buf_t *in) {
    if (in) {
        char path[] = "/tmp/hook-client-XXXXXX";
        int fd = mkstemp(path);
        if (fd >= 0) {
            unlink(path);
            for (size_t off = 0; off < in->len;) {
                ssize_t w = write(fd, in->data + off, in->len - off);
                if (w <= 0) break;
                off += (size_t)w;
            }
            lseek(fd, 0, SEEK_SET);
            dup2(fd, 0);
            close(fd);
        }
    }
    char **args = calloc((size_t)argc + 4, sizeof *args);
    args[0] = "python3";
    args[1] = "-I";
    args[2] = "-S";
    for (int i = 1; i < argc; i++) args[i + 2] = argv[i];
    execvp("python3", args);
    fprintf(stderr, "hook-client: hookd unreachable and python3 not runnable; the guards did not run\n");
    _exit(argc > 2 && strcmp(argv[2], "PreToolUse") == 0 ? 2 : 1);
}

static int sendall(int fd, const char *p, size_t n) {
    while (n) {
        ssize_t w = write(fd, p, n);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return -1;
        p += w;
        n -= (size_t)w;
    }
    return 0;
}

static int read_field(int fd, buf_t *b, size_t *pos, char **out) {
    for (;;) {
        char *nul = memchr(b->data + *pos, 0, b->len - *pos);
        if (nul) {
            *out = b->data + *pos;
            *pos = (size_t)(nul - b->data) + 1;
            return 0;
        }
        char tmp[65536];
        ssize_t r = read(fd, tmp, sizeof tmp);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return -1;
        put(b, tmp, (size_t)r);
    }
}

static int read_bytes(int fd, buf_t *b, size_t pos, size_t n) {
    while (b->len < pos + n) {
        char tmp[65536];
        ssize_t r = read(fd, tmp, sizeof tmp);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return -1;
        put(b, tmp, (size_t)r);
    }
    return 0;
}

int main(int argc, char **argv) {
    signal(SIGPIPE, SIG_IGN);
    char caller[32];
    snprintf(caller, sizeof caller, "%d", (int)getppid());
    setenv("HOOK_CALLER_PID", caller, 1);
    if (argc < 3) {
        fprintf(stderr, "hook-client: usage: hook-client <hook-dispatch.py> <event> <matcher> [<rev>]\n");
        return 2;
    }
    const char *home = getenv("HOME");
    const char *sock = getenv("HOOKD_SOCKET");
    char path[sizeof(((struct sockaddr_un *)0)->sun_path)];
    if (sock) snprintf(path, sizeof path, "%s", sock);
    else snprintf(path, sizeof path, "%s/.claude/hooks/dispatch/hookd.sock", home ? home : "");
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un addr = {0};
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof addr.sun_path, "%s", path);
    if (fd < 0 || connect(fd, (struct sockaddr *)&addr, sizeof addr) != 0) fallback(argc, argv, NULL);

    buf_t in = {0};
    char tmp[65536];
    for (;;) {
        ssize_t r = read(0, tmp, sizeof tmp);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        put(&in, tmp, (size_t)r);
    }
    buf_t req = {0};
    put_field(&req, "HOOKD1");
    put_num(&req, (size_t)argc - 2);
    for (int i = 2; i < argc; i++) put_field(&req, argv[i]);
    size_t envc = 0;
    for (char **e = environ; *e; e++) envc++;
    put_num(&req, envc);
    for (char **e = environ; *e; e++) put_field(&req, *e);
    char cwd[PATH_MAX];
    put_field(&req, getcwd(cwd, sizeof cwd) ? cwd : "/");
    put_num(&req, in.len);
    put(&req, in.data ? in.data : "", in.len);
    if (sendall(fd, req.data, req.len) != 0) fallback(argc, argv, &in);
    shutdown(fd, SHUT_WR);

    buf_t resp = {malloc(65536), 0, 65536};
    if (!resp.data) _exit(2);
    size_t pos = 0;
    char *f;
    if (read_field(fd, &resp, &pos, &f) != 0) fallback(argc, argv, &in);
    int code = atoi(f);
    if (read_field(fd, &resp, &pos, &f) != 0) fallback(argc, argv, &in);
    size_t out_len = strtoul(f, NULL, 10);
    if (read_bytes(fd, &resp, pos, out_len) != 0) fallback(argc, argv, &in);
    size_t out_pos = pos;
    pos += out_len;
    if (read_field(fd, &resp, &pos, &f) != 0) fallback(argc, argv, &in);
    size_t err_len = strtoul(f, NULL, 10);
    if (read_bytes(fd, &resp, pos, err_len) != 0) fallback(argc, argv, &in);
    sendall(1, resp.data + out_pos, out_len);
    sendall(2, resp.data + pos, err_len);
    return code;
}
