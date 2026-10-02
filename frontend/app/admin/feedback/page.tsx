"use client";

import Link from "next/link";
import { AdminFeedbackPanel } from "@/components/admin-feedback-panel";
import styles from "./page.module.css";

export default function FeedbackAdminPage() {
  return <main className={`container ${styles.page}`}>
    <Link href="/admin">← 관리자</Link>
    <AdminFeedbackPanel />
  </main>;
}
