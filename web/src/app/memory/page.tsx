// Memory: the user list. Each row links to that user's profile.

import Link from "next/link";

import { Nav } from "@/components/Nav";
import { listUsers } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function MemoryPage() {
  let users: Awaited<ReturnType<typeof listUsers>>["users"] = [];
  let error: string | null = null;
  try {
    users = (await listUsers()).users;
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <main>
      <Nav title="Memory" />
      {error ? (
        <p className="error">{error}</p>
      ) : users.length === 0 ? (
        <p className="muted">No users with memory yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Facts</th>
              <th>Episodes</th>
              <th>Sessions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.user_id}>
                <td>
                  <Link href={`/memory/${encodeURIComponent(u.user_id)}`}>{u.user_id}</Link>
                </td>
                <td>{u.fact_count}</td>
                <td>{u.episode_count}</td>
                <td>{u.session_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
