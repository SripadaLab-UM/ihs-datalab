x <- read.csv(inputs$IHS_SYN.DAILY_WEARABLE)
write.csv(fakeDataR::weekly_steps(x), outputs$weekly, row.names = FALSE)
datalab_count("rows_in", nrow(x))
