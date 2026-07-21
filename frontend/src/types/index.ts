export type ChatStatus =
  | 'success'
  | 'no_result'
  | 'constraint_mismatch'
  | 'insufficient_evidence'
  | 'conflicting_evidence'
  | 'stale_only'
  | 'out_of_scope'
  | 'clarification_required'
  | 'safety_refusal'
  | 'service_error'

export type AnswerMode = 'faq' | 'action_guide' | 'deterministic' | 'generated' | 'search_results_only' | 'department_handoff'

export interface Department { name: string | null; contactPerson?: string | null; contactRole?: string | null; phone: string | null; email?: string | null; officeLocation?: string | null; officeHours: string | null; contactDuty?: string | null; contactSource?: string | null; sourceUrl?: string | null }
export interface ImportantDate { label: string; start?: string | null; end?: string | null; description?: string | null; sourceLocator?: string | null }
export interface AdditionalFact { factType: string; label: string; value: string; appliesTo: string[]; studentActionable: boolean; sourceType: string; sourceLocator?: string | null; confidence: number }
export interface AttachmentManifest { name: string; url: string; sha256?: string | null; contentType?: string | null; extractionStatus: string; extractedCharacters: number }
export interface Notice {
  id: number
  title: string
  category?: string
  publishedAt: string
  noticeStatus: string
  statusLabel?: string | null
  sourceUrl: string
  score?: number
  evidenceExcerpt?: string
  isSample?: boolean
}
export interface NoticeDetailMetadata {
  category: string
  subCategory: string | null
  academicYear: number | null
  semester: number | null
  applicationStart: string | null
  applicationEnd: string | null
  eventStart?: string | null
  eventEnd?: string | null
  actionType?: string
  applicationMethod?: string | null
  applicationLocation: string | null
  targetStudentTypes?: string[]
  targetGrades?: number[]
  targetDepartments?: string[]
  targetCampus?: string[]
  eligibilityNotes?: string[]
  feeInformation: string | null
  capacity: string | null
  selectionMethod: string | null
  resultAnnouncement: string | null
  cancellationPolicy: string | null
  benefits?: string[]
  creditsOrHours?: string | null
  importantDates?: ImportantDate[]
  additionalFacts?: AdditionalFact[]
  evidenceMap?: Record<string, string>
  department: Department
  keywords: string[]
  requiredDocuments: string[]
}
export interface NoticeDetail {
  id: number
  sourceId: string
  title: string
  content: string
  publishedAt: string
  sourceUrl: string
  noticeStatus: string
  statusLabel?: string | null
  isArchived: boolean
  actionGuide: ActionGuide | null
  attachments?: AttachmentManifest[]
  metadata: NoticeDetailMetadata | null
}
export interface QuerySubQuery { taskKey: string; taskName?: string | null; queryText: string }
export interface QueryFilters { intent?: string; taskKey?: string; requestedTasks?: string[]; requestedFields?: string[]; subQueries?: QuerySubQuery[]; category?: string; subCategory?: string; academicYear?: number; admissionYear?: number; semester?: number; grade?: number; keywords?: string[]; intentConfidence?: number; needsClarification?: boolean; clarificationQuestion?: string | null; clarificationOptions?: string[]; followUp?: boolean; contextApplied?: boolean }
export interface NextAction { label: string; description?: string; url?: string; deadline?: string; official: boolean }
export interface AnswerFact { label: string; value: string; sourceNoticeId?: number | null; taskUnitId?: number | null; sourceLocator?: string | null }
export interface ActionGuideStep {
  order: number
  title: string
  description: string
  actionType: 'open_url' | 'navigate' | 'submit' | 'upload' | 'pay' | 'verify' | 'contact' | 'other'
  actionUrl?: string | null
  linkLabel?: string | null
  sourceType: string
  sourceLocator?: string | null
  confidence: number
}
export interface ActionGuide {
  taskName: string
  summary?: string | null
  targets: string[]
  period: { start?: string | null; end?: string | null }
  prerequisites: string[]
  requiredDocuments: string[]
  eligibilityNotes?: string[]
  applicationMethod?: string | null
  applicationLocation?: string | null
  feeInformation?: string | null
  capacity?: string | null
  selectionMethod?: string | null
  resultAnnouncement?: string | null
  cancellationPolicy?: string | null
  benefits?: string[]
  creditsOrHours?: string | null
  importantDates?: ImportantDate[]
  additionalFacts?: AdditionalFact[]
  steps: ActionGuideStep[]
  warnings: string[]
  applicationUrl?: string | null
  sourceUrl: string
  department: Department
  confidence: number
  needsReview: boolean
}
export interface SourceEvidence { noticeId: number; title: string; publishedAt: string; effectiveStatus: string; evidenceExcerpt: string; url: string; taskKey?: string | null; taskUnitId?: number | null }
export interface AnswerMedia { type: 'image'; url: string; alt: string; caption?: string | null; sourceUrl: string; noticeId: number }
export interface TaskAnswerResult { taskKey: string; taskName: string; answer: string; answerFacts: AnswerFact[]; actionGuide?: ActionGuide | null; nextAction?: NextAction | null; department: Department; sourceNoticeIds: number[] }
export interface SearchScope { sources: string[]; noticeCount: number; description: string }
export interface ChatResponse {
  answerId: string
  answer: string
  status: ChatStatus
  answerMode: AnswerMode
  answerFacts?: AnswerFact[]
  answerNotes?: string[]
  clarificationOptions?: string[]
  matchedNotices: Notice[]
  sources: SourceEvidence[]
  media?: AnswerMedia[]
  department: Department
  nextAction?: NextAction | null
  actionGuide?: ActionGuide | null
  taskResults?: TaskAnswerResult[]
  warnings: string[]
  originalUrl: string | null
  hasData: boolean
  sessionId: string
  query?: QueryFilters
  verifiedAt: string
  searchScope: SearchScope
}
export interface ChatMessageModel {
  id: string
  role: 'assistant' | 'user'
  content: string
  answerId?: string
  answerMode?: AnswerMode
  answerFacts?: AnswerFact[]
  answerNotes?: string[]
  clarificationOptions?: string[]
  status?: ChatStatus
  verifiedAt?: string
  searchScope?: SearchScope
  warnings?: string[]
  nextAction?: NextAction | null
  actionGuide?: ActionGuide | null
  taskResults?: TaskAnswerResult[]
  department?: Department
  notices?: Notice[]
  media?: AnswerMedia[]
  hasData?: boolean
}
export interface FAQ { id: number; question: string; category: string }
export type Panel = 'category' | 'faq' | null
export type FeedbackReason = 'resolved' | 'incorrect' | 'outdated' | 'misunderstood' | 'insufficient' | 'needs_staff'
