import { cn } from "@/lib/utils";

export type AgentLbLogoProps = {
  className?: string;
  size?: number;
};

export function AgentLbLogo({ className, size = 32 }: AgentLbLogoProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      fill="none"
      viewBox="0 0 32 32"
      className={cn("shrink-0", className)}
    >
      <path
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2.484"
        d="M8.5 16h5m0 0 8.5-5.75M13.5 16l8.5 5.75M13.5 16H22M30.758 16c0 8.15-6.607 14.758-14.758 14.758-8.15 0-14.758-6.607-14.758-14.758C1.242 7.85 7.85 1.242 16 1.242c8.15 0 14.758 6.608 14.758 14.758Z"
      />
    </svg>
  );
}
