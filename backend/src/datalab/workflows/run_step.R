# DataLab's step wrapper for workflow runs. DataLab copies it into each run
# folder and mounts it read-only at /run/datalab. It reads the step contract,
# sets the random number generator in full, runs the step's script, and
# writes /run/result/result.json.
#
# The contract, all read-only except /run/out and /run/result:
#   /run/step/step.json   step id and type, seed, parameters, inputs (path,
#                         sha256, bytes, rows, columns), outputs (name -> path)
#   /run/step/script.R    the step's R code: inline `r:`, a pipeline's run.R,
#                         or a custom QC check
#   /run/in/<name>/       each declared input: an upstream step's outputs, or
#                         /run/in/oracle/ for a pipeline's extracts
#   /run/out/             write declared outputs here; nothing else comes back
#   /run/result/          result.json goes here (this wrapper writes it)
#
# In the script: `params`, `inputs$<name>` (a path), `outputs$<name>` (a path),
# plus datalab_count(), datalab_message() and datalab_check(). Messages and
# checks must hold counts and column names only: they go into the run record
# and are shown in the Workflows tab.
#
# Exit codes: 0 ok, 1 the script raised an error, 3 a check failed.

local({
  spec <- jsonlite::read_json("/run/step/step.json", simplifyVector = TRUE)
  .datalab <- new.env()
  .datalab$counts <- list()
  .datalab$messages <- list()
  .datalab$checks <- list()

  # One RNG, stated in full, so a change of R's defaults can't change results.
  RNGkind(kind = "Mersenne-Twister", normal.kind = "Inversion", sample.kind = "Rejection")
  set.seed(spec$seed)

  env <- new.env(parent = globalenv())
  env$params <- spec$params
  env$inputs <- lapply(spec$inputs, function(i) i$path)
  env$outputs <- spec$outputs
  env$datalab_count <- function(name, value) {
    .datalab$counts[[name]] <- value
    invisible(value)
  }
  env$datalab_message <- function(text, level = "info") {
    .datalab$messages[[length(.datalab$messages) + 1]] <- list(level = level, text = text)
    invisible(NULL)
  }
  env$datalab_check <- function(id, passed, observed = NULL, expected = NULL, message = "") {
    .datalab$checks[[length(.datalab$checks) + 1]] <- list(
      id = id, status = if (isTRUE(passed)) "pass" else "fail",
      observed = observed, expected = expected, message = message
    )
    invisible(isTRUE(passed))
  }

  write_result <- function(status, error = NULL) {
    msgs <- .datalab$messages
    if (!is.null(error)) msgs[[length(msgs) + 1]] <- list(level = "error", text = error)
    result <- list(
      status = status,
      step = spec$step,
      counts = .datalab$counts,
      messages = msgs,
      checks = .datalab$checks,
      r_version = R.version.string,
      rng_kind = RNGkind()
    )
    jsonlite::write_json(result, "/run/result/result.json", auto_unbox = TRUE,
                         null = "null", na = "null", digits = NA, pretty = TRUE)
  }

  ok <- tryCatch({
    sys.source("/run/step/script.R", envir = env, keep.source = FALSE)
    TRUE
  }, error = function(e) {
    write_result("failed", conditionMessage(e))
    FALSE
  })
  if (!ok) quit(save = "no", status = 1)
  failed <- any(vapply(.datalab$checks, function(c) c$status == "fail", logical(1)))
  write_result(if (failed) "failed" else "ok")
  quit(save = "no", status = if (failed) 3 else 0)
})
