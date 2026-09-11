# ---------------------------------------------------------------------------
# Cost and retention guardrails.
#
# The account already burned $97.91 on Neptune Analytics in three days and has
# spent 74% of a $200 budget. A budget existed; nothing shouted. These make it
# shout.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "budget_alerts" {
  count = length(var.budget_notification_emails) > 0 ? 1 : 0

  name = "${local.name_prefix}-budget-alerts"
}

resource "aws_sns_topic_subscription" "budget_alerts" {
  for_each = toset(var.budget_notification_emails)

  topic_arn = aws_sns_topic.budget_alerts[0].arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_budgets_budget" "monthly" {
  count = length(var.budget_notification_emails) > 0 ? 1 : 0

  name         = "${local.name_prefix}-monthly"
  budget_type  = "COST"
  limit_amount = var.budget_limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # 50 and 80 on actual spend; 100 on forecast so it fires before the money is
  # already gone - which is how the Neptune bill went unnoticed.
  dynamic "notification" {
    for_each = [50, 80]

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts[0].arn]
      subscriber_email_addresses = var.budget_notification_emails
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_sns_topic_arns  = [aws_sns_topic.budget_alerts[0].arn]
    subscriber_email_addresses = var.budget_notification_emails
  }
}
