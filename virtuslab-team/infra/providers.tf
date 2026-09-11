provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = local.common_tags
  }
}

# hashicorp/external needs no configuration; the CLI invocations in data.tf
# carry their own --profile and --region.
