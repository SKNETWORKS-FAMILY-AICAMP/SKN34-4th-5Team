import styles from "./member-name.module.css";

const rankClasses: Record<number, string> = {
  1: styles.rank1,
  2: styles.rank2,
  3: styles.rank3,
  4: styles.rank4,
  5: styles.rank5,
};

type Props = {
  name: string;
  rank?: number | null;
  suffix?: string;
  size?: "normal" | "small" | "community";
};

export function MemberName({ name, rank, suffix = "", size = "normal" }: Props) {
  const rankClass = typeof rank === "number" ? rankClasses[rank] : undefined;
  const sizeClass = size === "small" ? styles.small : size === "community" ? styles.community : "";
  return <span className={`${styles.name} ${sizeClass}`}>
    {rankClass && <span className={`${styles.rank} ${rankClass}`} role="img" aria-label={`${rank}등급`} />}
    <span>{name}{suffix}</span>
  </span>;
}
