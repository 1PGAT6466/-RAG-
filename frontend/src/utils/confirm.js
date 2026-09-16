import { ElMessageBox } from 'element-plus'
export function confirmDelete(message = '确定要删除吗？此操作不可撤销。') {
  return ElMessageBox.confirm(message, '确认删除', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    type: 'warning',
    confirmButtonClass: 'el-button--danger',
  })
}
