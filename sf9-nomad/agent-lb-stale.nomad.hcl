job "agent-lb-stale" {
  type        = "batch"
  node_pool   = "factory"
  datacenters = ["*"]

  periodic {
    crons            = ["0 5 * * *"]
    time_zone        = "UTC"
    prohibit_overlap = true
  }

  constraint {
    attribute = "${node.class}"
    value     = "linux"
  }

  group "job" {
    restart {
      attempts = 0
      mode     = "fail"
    }
    reschedule {
      attempts  = 0
      unlimited = false
    }

    task "run" {
      driver = "raw_exec"
      user   = "jobs"
      config {
        command = "/usr/bin/python3"
        args = [
          "/home/jobs/factory/bin/actions-run", "run",
          "--repo", "agent-lb",
          "--origin", "https://github.com/aneym/agent-lb.git",
          "--ref", "main",
          "--timeout", "600",
          "--", "bash", "scripts/jobs/stale.sh"
        ]
      }
      resources {
        cpu    = 100
        memory = 256
      }
    }
  }
}
