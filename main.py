#!/usr/bin/env python3
"""
Multi-platform newsletter system entry point.
Routes to platform-specific implementations based on CLI flags.
"""

import argparse
import sys
from common_utils import set_up_logging


def main():
    parser = argparse.ArgumentParser(description='Multi-platform newsletter system')
    parser.add_argument('--platform', required=True, choices=['twitter', 'discord'],
                       help='Platform to process (twitter or discord)')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Print to console only, do not send email')
    parser.add_argument('--send', action='store_true',
                       help='Actually send email')
    parser.add_argument('--window', type=int, 
                       help='Override window hours from config')
    parser.add_argument('--no-db', action='store_true',
                       help='Skip database operations (for testing email only)')
    parser.add_argument('--to', required=True,
                       help='Recipient email address for the newsletter')
    parser.add_argument('--config', default='./config.yaml',
                       help='Path to config file (default: ./config.yaml)')
    parser.add_argument('--secrets', default='/home/mywang/Code/secrets/kestral.yaml',
                       help='Path to secrets file (default: /home/mywang/Code/secrets/kestral.yaml)')
    parser.add_argument('--account-lists', nargs='*',
                       help='Process only specified account lists (default: all)')

    args = parser.parse_args()

    # Set up logging for this platform
    logger = set_up_logging(args.platform)

    if not args.dry_run and not args.send:
        logger.error("Must specify either --dry-run or --send")
        return

    # Log the command being run
    cmd_args = " ".join(sys.argv[1:])
    logger.info(f"Command: {cmd_args}")

    # Route to platform-specific implementation
    if args.platform == 'twitter':
        import twitter
        twitter.main(dry_run=args.dry_run, window_hours=args.window, no_db=args.no_db,
                    recipient_email=args.to, config_path=args.config, secrets_path=args.secrets,
                    account_lists_filter=getattr(args, 'account_lists', None), logger=logger)
    elif args.platform == 'discord':
        # Future implementation
        logger.info("Discord support coming soon! Use --platform=twitter for now.")
        return

    logger.info("Newsletter processing complete!")


if __name__ == '__main__':
    main()