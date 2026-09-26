# Small helpers for the practice analysis.

# Minutes between a bedtime and a wake time given as "HH:MM", across midnight.
sleep_minutes <- function(bed, wake) {
  to_min <- function(x) {
    parts <- strsplit(x, ":", fixed = TRUE)
    vapply(parts, function(p) as.numeric(p[1]) * 60 + as.numeric(p[2]), numeric(1))
  }
  (to_min(wake) - to_min(bed)) %% (24 * 60)
}
