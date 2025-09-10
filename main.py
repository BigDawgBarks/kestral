#!/usr/bin/env python3
"""
Multi-platform newsletter system entry point.
Routes to platform-specific implementations based on CLI flags.
"""

import argparse


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
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.send:
        print("Error: Must specify either --dry-run or --send")
        return
    
    # Route to platform-specific implementation
    if args.platform == 'twitter':
        import twitter
        twitter.main(dry_run=args.dry_run, window_hours=args.window)
    elif args.platform == 'discord':
        # Future implementation
        print("Discord support coming soon! Use --platform=twitter for now.")
        return
    
    print("Newsletter processing complete!")


if __name__ == '__main__':
    main()