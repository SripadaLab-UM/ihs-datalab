weekly_steps <- function(x) {
  x$week <- format(as.Date(substr(x$RECORD_DATE, 1, 10)), "%G-W%V")
  dplyr::summarise(dplyr::group_by(x, DEVICE, week),
                   mean_steps = mean(STEPS, na.rm = TRUE), .groups = "drop")
}

run_pipeline <- function(name) {
  script <- system.file("pipelines", name, "run.R", package = "fakeDataR", mustWork = TRUE)
  sys.source(script, envir = parent.frame())
}
