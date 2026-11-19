import static net.logstash.logback.argument.StructuredArguments.kv;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

class Example {
    private static final Logger log = LoggerFactory.getLogger(Example.class);

    void run(int n, String user, String pw, Exception e) {
        // ruleid: logwarden-event-name-java
        log.info(String.format("Imported %d rows", n));
        // ruleid: logwarden-event-name-java
        log.error("failed for " + user);
        // ruleid: logwarden-event-name-java
        log.info("Imported {} rows", n);
        // ok: logwarden-event-name-java
        log.info("import_completed", kv("rows_imported", n));

        // ruleid: logwarden-sensitive-key-java
        log.info("login_attempted", kv("password", pw));
        // ruleid: logwarden-sensitive-key-java
        MDC.put("auth_token", pw);
        // ok: logwarden-sensitive-key-java
        log.info("login_attempted", kv("user_id", user));

        // ruleid: logwarden-banned-key-java
        log.error("upstream_call_failed", kv("status", 502));
        // ok: logwarden-banned-key-java
        log.error("upstream_call_failed", kv("http.response.status_code", 502), e);

        // ruleid: logwarden-print-java
        System.out.println("starting");
        // ruleid: logwarden-print-java
        e.printStackTrace();
        // ok: logwarden-print-java
        log.info("service_started");
    }
}
